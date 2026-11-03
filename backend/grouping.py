"""Evidence-preserving display groups. These are not new facts or continuous states."""
from bisect import bisect_right
from collections import defaultdict
from typing import Literal, TypedDict


class EventGroup(TypedDict):
    id: str
    kind: Literal['single', 'repeated_observations', 'overlapping_actions', 'consecutive_actions']
    event_ids: list[str]
    evidence_ids: list[str]
    start_ms: int
    end_ms: int


OBSERVATION_GAP_MS = 60000
ACTION_TOLERANCE_MS = 2000
PERSON_ACTIVITY_GAP_MS = 1000
PERSON_ACTIVITIES = {'walk', 'climb', 'lying_on_floor'}


def duplicate_clusters(document: dict, events: list[dict]) -> list[list[dict]]:
    """Match duplicate candidates without erasing co-occurring activity or sources.

    Every member must share an interval and an evidence time with the entire
    cluster. A chain of pairwise matches alone does not identify one occurrence.
    """
    entities = {e['id']: e for e in document['entities']}
    evidence = {e['id']: e for e in document['evidence']}
    frames = {f['id']: f for f in document['frames']}
    by_subject = defaultdict(list)
    for event in events:
        by_subject[event['subject_id']].append(event)

    def support(event):
        return {frames[f]['pts_ms'] for ref in event['evidence_ids']
                for f in evidence.get(ref, {}).get('frame_ids', []) if f in frames
                and event['start_ms'] <= frames[f]['pts_ms'] <= event['end_ms']}

    def eligible(event):
        return (event['review_status'] != 'human_reviewed' and event['epistemic_type'] != 'unknown'
                and event['action'] not in {'unknown', 'occluded'}
                and all(entities.get(e, {}).get('identity_status') == 'distinct_candidate'
                        for e in [event['subject_id'], event['target_id']] if e))

    def fall_candidate(event):
        # Location is optional context for a fall, not a second affected object.
        # Epistemic labels remain on each source; only the occurrence is shared.
        return (event['action'] == 'fall'
                and entities.get(event['subject_id'], {}).get('entity_type') == 'person'
                and event['epistemic_type'] in {'observed', 'inferred'}
                and (not event.get('target_id')
                     or entities.get(event['target_id'], {}).get('entity_type') == 'location'))

    def barrier(first, current):
        # Seeing a subject is compatible with a sampled action. A fall and the
        # resulting floor posture can coexist, but walking/standing reset it.
        compatible = {first['action']}
        if first['action'] != 'seen_at':
            compatible.add('seen_at')
        if first['action'] in {'fall', 'lying_on_floor'}:
            compatible.update({'fall', 'lying_on_floor'})
        for other in by_subject[first['subject_id']]:
            if other['id'] in {first['id'], current['id']}:
                continue
            if (other['end_ms'] < first['start_ms']
                    or (other['end_ms'] == first['start_ms'] and other['start_ms'] < first['start_ms'])
                    or other['start_ms'] > max(first['end_ms'], current['end_ms'])):
                continue
            if (first['action'] in {'smoke', 'flame', 'sparks'}
                    and other['action'] in {'smoke', 'flame', 'sparks'}
                    and other['action'] != first['action']
                    and other['epistemic_type'] != 'unknown' and other['review_status'] != 'human_reviewed'):
                continue
            if (fall_candidate(first) and other['action'] in {'seen_at', 'lying_on_floor', 'fall'}
                    and other['epistemic_type'] in {'observed', 'inferred'}
                    and other['review_status'] == first['review_status']):
                if other['action'] != 'fall' or (fall_candidate(other) and not (
                        first.get('target_id') and other.get('target_id')
                        and first['target_id'] != other['target_id'])):
                    continue
            if (other['epistemic_type'] != first['epistemic_type']
                    or other['review_status'] != first['review_status']
                    or other['action'] not in compatible
                    or (other['action'] == first['action'] and other['target_id'] != first['target_id'])):
                return True
        return False

    clusters, candidates = [], defaultdict(list)
    for event in events:
        signature = (('fall_occurrence', event['subject_id'], event['review_status']) if fall_candidate(event)
                     else tuple(event.get(k) for k in ('subject_id', 'target_id', 'action',
                                                      'epistemic_type', 'rule_id', 'review_status')))
        times = support(event)
        match = None
        if eligible(event) and times:
            # Expire finished candidates so long recordings do not scan all
            # earlier occurrences of the same action.
            candidates[signature] = [c for c in candidates[signature] if c['end'] >= event['start_ms']]
            for candidate in reversed(candidates[signature]):
                start = max(candidate['start'], event['start_ms'])
                end = min(candidate['end'], event['end_ms'])
                overlap = start < end or (start == end and all(
                    e['start_ms'] == e['end_ms'] == start for e in [*candidate['members'], event]))
                compatible_location = (not fall_candidate(event) or len({e['target_id'] for e in
                    [*candidate['members'], event] if e.get('target_id')}) <= 1)
                if (overlap and candidate['times'] & times and compatible_location
                        and not barrier(candidate['members'][0], event)):
                    match = candidate
                    break
        if match is None:
            members = [event]
            clusters.append(members)
            if eligible(event) and times:
                candidates[signature].append({'members': members, 'start': event['start_ms'],
                                              'end': event['end_ms'], 'times': times})
        else:
            match['members'].append(event)
            match.update(start=max(match['start'], event['start_ms']),
                         end=min(match['end'], event['end_ms']), times=match['times'] & times)
    return clusters


def build_event_groups(document: dict, job: dict, as_of_ms: int | None = None) -> list[EventGroup]:
    """Group only compatible existing IDs; preserve every source, review and evidence.

    Repeated observations mean multiple sampled sightings, never continuous presence.
    Adjacent sampled person activities may share a display interval, not a claim
    of uninterrupted activity. Other actions require shared actual evidence times
    and close endpoints. Human-reviewed or ambiguous entities stay
    separate. All events (including unknown/inferred) participate as boundaries.
    """
    cutoff = document['video_duration_ms'] if as_of_ms is None else as_of_ms
    events = sorted((e for e in document['events'] if e['end_ms'] <= cutoff),
                    key=lambda e: (e['start_ms'], e['end_ms'], e['id']))
    sources = {}
    representatives = []
    for members in duplicate_clusters(document, events):
        first = members[0]
        sources[first['id']] = members
        representatives.append({**first, 'start_ms': min(e['start_ms'] for e in members),
                                'end_ms': max(e['end_ms'] for e in members),
                                'evidence_ids': list(dict.fromkeys(ref for e in members for ref in e['evidence_ids']))})
    events = sorted(representatives, key=lambda e: (e['start_ms'], e['end_ms'], e['id']))
    identities = {e['id']: e['identity_status'] for e in document['entities']}
    entity_types = {e['id']: e.get('entity_type') for e in document['entities']}
    evidence = {e['id']: e for e in document['evidence']}
    times = {f['id']: f['pts_ms'] for f in document['frames']}
    ranges = []
    for r in sorted(job.get('analyzed_ranges', []), key=lambda r: r['start_ms']):
        if ranges and r['start_ms'] <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], r['end_ms'])
        else:
            ranges.append([r['start_ms'], r['end_ms']])
    range_starts = [r[0] for r in ranges]

    def covered(start, end):
        if end <= start:
            return True
        if not ranges:
            return job.get('status') == 'completed'
        index = bisect_right(range_starts, start)-1
        return index >= 0 and ranges[index][1] >= end

    def key(event, *, activity=False):
        return tuple(event.get(k) for k in ['subject_id', 'target_id', 'action',
                                            'epistemic_type', 'rule_id', 'review_status'] if not (activity and k == 'target_id'))

    def mergeable(event):
        return (event['review_status'] != 'human_reviewed'
                and event['epistemic_type'] != 'unknown'
                and event['action'] not in {'unknown', 'occluded'}
                and all(identities.get(identifier) == 'distinct_candidate'
                        for identifier in [event['subject_id'], event['target_id']] if identifier))

    def frame_times(event):
        return {times[f] for ref in event['evidence_ids']
                for f in evidence.get(ref, {}).get('frame_ids', []) if f in times}

    def person_activity(event):
        return (entity_types.get(event['subject_id']) == 'person'
                and event['action'] in PERSON_ACTIVITIES and event['epistemic_type'] == 'observed'
                and (not event['target_id'] or entity_types.get(event['target_id']) == 'location'))

    groups: list[list[dict]] = []
    active: dict[str, list[dict]] = {}
    watchers: dict[str, set[str]] = defaultdict(set)
    # A long overlapping movement/uncertainty must not be skipped by later sightings.
    blocked_until: dict[str, int] = {}

    def forget(subject):
        previous = active.pop(subject, None)
        if previous and previous[0]['target_id']:
            watchers[previous[0]['target_id']].discard(subject)

    for event in events:
        subject = event['subject_id']
        if event['action'] != 'seen_at' or event['epistemic_type'] != 'observed':
            for watching in list(watchers.get(subject, set())):
                forget(watching)
        previous = active.get(subject)
        joined = False
        same_activity = bool(previous and person_activity(event) and person_activity(previous[0])
                             and key(event, activity=True) == key(previous[0], activity=True)
                             and len({e['target_id'] for e in [*previous,event] if e['target_id']}) <= 1)
        if previous and mergeable(event) and mergeable(previous[0]) and (same_activity or key(event) == key(previous[0])):
            first, last = previous[0], previous[-1]
            if event['action'] == 'seen_at' and event['epistemic_type'] == 'observed':
                joined = (event['start_ms']-last['end_ms'] <= OBSERVATION_GAP_MS
                          and first['start_ms'] >= blocked_until.get(subject, -1)
                          and covered(last['end_ms'], event['start_ms']))
            elif person_activity(event):
                end = max(e['end_ms'] for e in previous)
                joined = (event['start_ms']-end <= PERSON_ACTIVITY_GAP_MS
                          and blocked_until.get(subject, -1) <= end
                          and covered(first['start_ms'], max(end, event['end_ms']))
                          and bool(frame_times(last)) and bool(frame_times(event)))
            elif event['action'] != 'seen_at':
                joined = (len(sources[first['id']]) == len(sources[event['id']]) == 1
                          and abs(event['start_ms']-first['start_ms']) <= ACTION_TOLERANCE_MS
                          and abs(event['end_ms']-first['end_ms']) <= ACTION_TOLERANCE_MS
                          and max(event['start_ms'], first['start_ms']) <= min(event['end_ms'], first['end_ms'])
                          and bool(frame_times(first) & frame_times(event)))
        if joined:
            previous.append(event)
        else:
            forget(subject)
            previous = [event]
            groups.append(previous)
            active[subject] = previous
            if event['target_id']:
                watchers[event['target_id']].add(subject)
        if event['action'] != 'seen_at' or event['epistemic_type'] != 'observed':
            blocked_until[subject] = max(blocked_until.get(subject, -1), event['end_ms'])

    result = []
    for group in groups:
        members = sorted((source for e in group for source in sources[e['id']]),
                         key=lambda e: (e['start_ms'], e['end_ms'], e['id']))
        kind = ('single' if len(members) == 1 else 'overlapping_actions' if len(group) == 1
                else 'repeated_observations' if members[0]['action'] == 'seen_at'
                else 'consecutive_actions' if person_activity(members[0]) else 'overlapping_actions')
        result.append({'id': 'group:'+members[0]['id'], 'kind': kind,
                       'event_ids': [e['id'] for e in members],
                       'evidence_ids': list(dict.fromkeys(ref for e in members for ref in e['evidence_ids'])),
                       'start_ms': min(e['start_ms'] for e in members), 'end_ms': max(e['end_ms'] for e in members)})
    return result

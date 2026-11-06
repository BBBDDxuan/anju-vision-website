"""Offline reciprocal wrist motion in shoulder-relative coordinates.

This is a transparent temporal heuristic, not a trained action classifier.
Each track/side is segmented before analysis; uncertain/lowered wrists and gaps
terminate continuity. Static raising, body translation and small jitter cannot
satisfy three alternating sweeps. Boundaries are retrospective, confirmation
time records when enough motion had actually been observed.
"""
import math
from statistics import median

RULE = {'name': 'pose-based heuristic', 'version': 'shoulder-relative-reciprocal-v1',
        'minimumKeypointConfidence': .65, 'minimumShoulderWidthPx': 8,
        'wristAboveShoulderWidths': .1, 'maximumElbowBelowShoulderWidths': .3,
        'maximumGapMs': 100, 'medianSamples': 3, 'minimumSweepShoulderWidths': .35,
        'minimumAlternatingSweeps': 3, 'minimumDurationMs': 650,
        'maximumSweepDurationMs': 900, 'trailWindowMs': 600}


def arm_sample(person, side):
    k = person['keypoints']
    shoulder, elbow, wrist = (5, 7, 9) if side == 'left' else (6, 8, 10)
    required = [k[i] for i in [5, 6, elbow, wrist]]
    if any(len(p) != 3 or not all(math.isfinite(v) for v in p) or p[2] < .65 for p in required):
        return None
    scale = math.dist(k[5][:2], k[6][:2])
    if scale < RULE['minimumShoulderWidthPx']:
        return None
    if (k[wrist][1] >= k[shoulder][1] - .1 * scale
            or k[elbow][1] > k[shoulder][1] + .3 * scale):
        return None
    return {'relativeX': (k[wrist][0] - k[shoulder][0]) / scale,
            'relativeY': (k[wrist][1] - k[shoulder][1]) / scale,
            'wrist': k[wrist], 'keypointId': wrist}


def reciprocal_segments(samples):
    if len(samples) < 4:
        return []
    # Causal median avoids using later frames to validate an earlier sample.
    values = [median([s['relativeX'] for s in samples[max(0, i - 2):i + 1]]) for i in range(len(samples))]
    threshold = RULE['minimumSweepShoulderWidths']
    pivots = [(0, samples[0]['timeMs'])]
    direction, extreme = 0, 0
    for i in range(1, len(samples)):
        delta = values[i] - values[extreme]
        if direction == 0:
            if abs(delta) >= threshold:
                direction = 1 if delta > 0 else -1
                extreme = i
        elif delta * direction >= 0:
            extreme = i
        elif -delta * direction >= threshold:
            # A pivot is supported by a real excursion on either side.
            pivots.append((extreme, samples[i]['timeMs']))
            extreme, direction = i, -direction
    if direction and abs(values[extreme] - values[pivots[-1][0]]) >= threshold:
        pivots.append((extreme, samples[extreme]['timeMs']))
    qualifying = []
    for j in range(3, len(pivots)):
        group = pivots[j - 3:j + 1]
        indices = [p[0] for p in group]
        durations = [samples[b]['timeMs'] - samples[a]['timeMs'] for a, b in zip(indices, indices[1:])]
        deltas = [values[b] - values[a] for a, b in zip(indices, indices[1:])]
        if (sum(durations) < RULE['minimumDurationMs'] or max(durations) > RULE['maximumSweepDurationMs']
                or any(abs(d) < threshold for d in deltas)
                or any(a * b >= 0 for a, b in zip(deltas, deltas[1:]))):
            continue
        qualifying.append({'startMs': samples[indices[0]]['timeMs'],
            'endMs': samples[indices[-1]]['validUntilMs'],
            'confirmedAtMs': group[-1][1],
            'sweepCount': 3})
    merged = []
    for event in qualifying:
        if merged and event['startMs'] <= merged[-1]['endMs']:
            merged[-1]['endMs'] = max(merged[-1]['endMs'], event['endMs'])
            merged[-1]['sweepCount'] += 1
        else:
            merged.append(dict(event))
    return merged


def analyze_motion(frames):
    ids = sorted({p['id'] for f in frames for p in f['people']})
    side_events = []
    for track in ids:
        for side in ['left', 'right']:
            segment = []
            def flush():
                side_events.extend(dict(e, trackId=track, side=side) for e in reciprocal_segments(segment))
                segment.clear()
            for f in frames:
                p = next((p for p in f['people'] if p['id'] == track), None)
                sample = arm_sample(p, side) if p else None
                if sample is None:
                    flush()
                    continue
                if segment and f['timeMs'] - segment[-1]['timeMs'] > RULE['maximumGapMs']:
                    flush()
                segment.append(dict(sample, timeMs=f['timeMs'], validUntilMs=f['validUntilMs']))
            flush()
    # Merge simultaneous sides for the same person into one observed action.
    events = []
    for e in sorted(side_events, key=lambda e: (e['trackId'], e['startMs'])):
        previous = events[-1] if events else None
        if previous and previous['trackId'] == e['trackId'] and e['startMs'] < previous['endMs']:
            previous['startMs'] = min(previous['startMs'], e['startMs'])
            previous['endMs'] = max(previous['endMs'], e['endMs'])
            previous['confirmedAtMs'] = min(previous['confirmedAtMs'], e['confirmedAtMs'])
            previous['sideIntervals'].append(e)
        else:
            events.append({'id': f'wave-{len(events) + 1}', 'type': 'wave', 'trackId': e['trackId'],
                'startMs': e['startMs'], 'endMs': e['endMs'], 'confirmedAtMs': e['confirmedAtMs'],
                'sideIntervals': [e], 'method': RULE['name'], 'helpIntentEstablished': False})
    return events

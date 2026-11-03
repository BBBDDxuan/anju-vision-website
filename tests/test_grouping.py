"""Synthetic grouping fixtures only. No real model results or paid calls."""
import copy

import pytest

from backend.answer import query_memory
from backend.grouping import build_event_groups


def event(identifier, start, end=None, *, action='seen_at', target='table', kind='observed', frames=None):
    end = start if end is None else end
    return {'id': identifier, 'video_id': 'synthetic', 'start_ms': start, 'end_ms': end,
            'subject_id': 'item', 'target_id': target, 'action': action, 'epistemic_type': kind,
            'review_status': 'model_unreviewed', 'evidence_ids': ['ev_'+identifier],
            'source_event_ids': [], 'rule_id': 'visual_context_v1' if kind == 'inferred' else None,
            'model_score': None, 'uncertainty_reasons': ['Synthetic test reason'] if kind != 'observed' else [],
            '_times': frames if frames is not None else sorted({start, end})}


def memory(*events):
    events = copy.deepcopy(events)
    times = set(); evidence = []
    for e in events:
        pts = e.pop('_times'); times.update(pts)
        evidence.append({'id': e['evidence_ids'][0], 'video_id': 'synthetic',
                         'start_ms': e['start_ms'], 'end_ms': e['end_ms'], 'frame_ids': [f'f_{t}' for t in pts]})
    entities = [{'id': identifier, 'label': 'TEST_'+identifier, 'aliases': [], 'appearance': 'Synthetic fixture',
                 'identity_status': 'distinct_candidate'} for identifier in ['item', 'table', 'bag', 'other']]
    return {'schema_version': '0.1', 'fixture_kind': 'manual_design_example', 'video_id': 'synthetic',
            'video_duration_ms': 300000, 'entities': entities, 'events': events, 'evidence': evidence,
            'frames': [{'id': f'f_{t}', 'pts_ms': t, 'kind': 'manual_placeholder'} for t in sorted(times)]}


DONE = {'status': 'completed', 'is_mock': True}


def person_memory(*events):
    doc = memory(*events)
    doc['entities'][0].update(entity_type='person', label='测试人物1')
    for entity in doc['entities'][1:]:
        entity['entity_type'] = 'location'
    return doc


def test_adjacent_person_activity_combines_time_range_without_shared_frames():
    doc = person_memory(event('a',52000,56000,action='walk'), event('b',56000,60000,action='walk'),
                        event('c',60000,64000,action='walk'))
    before = copy.deepcopy(doc)
    groups = build_event_groups(doc,DONE)
    assert len(groups) == 1 and groups[0]['kind'] == 'consecutive_actions'
    assert groups[0]['event_ids'] == ['a','b','c']
    assert (groups[0]['start_ms'],groups[0]['end_ms']) == (52000,64000)
    assert groups[0]['evidence_ids'] == ['ev_a','ev_b','ev_c']
    assert doc == before
    answer = query_memory(doc,DONE,'测试人物1的经过是什么？',entity_id='item')
    assert len(answer['observations']) == 1
    assert '3条相邻行为记录' in answer['answer'] and '未确认采样间动作始终连续' in answer['answer']
    assert '共享证据' not in answer['answer']


@pytest.mark.parametrize('boundary', ['different_person','different_action','different_target','gap','coverage',
                                     'unknown','ambiguous','human','inferred','contradicting_interval'])
def test_person_activity_respects_boundaries(boundary):
    doc = person_memory(event('a',52000,56000,action='walk'),event('b',56000,60000,action='walk'))
    job = dict(DONE)
    if boundary == 'different_person': doc['events'][1]['subject_id'] = 'other'
    if boundary == 'different_action': doc['events'][1]['action'] = 'sit_down'
    if boundary == 'different_target': doc['events'][1]['target_id'] = 'bag'
    if boundary == 'gap': doc['events'][1]['start_ms'] = 58000
    if boundary == 'coverage': job.update(status='partial',analyzed_ranges=[{'start_ms':52000,'end_ms':56000}])
    if boundary == 'unknown': doc['events'][1].update(action='unknown',epistemic_type='unknown')
    if boundary == 'ambiguous': doc['entities'][0]['identity_status'] = 'ambiguous'
    if boundary == 'human': doc['events'][1]['review_status'] = 'human_reviewed'
    if boundary == 'inferred': doc['events'][1].update(epistemic_type='inferred',rule_id='visual_context_v1')
    if boundary == 'contradicting_interval':
        boundary_event = copy.deepcopy(doc['events'][0])
        boundary_event.update(id='boundary',start_ms=50000,end_ms=65000,action='sit_down')
        doc['events'] = [boundary_event,*doc['events']]
    assert all(len(g['event_ids']) == 1 for g in build_event_groups(doc,job))


def test_adjacent_discrete_person_actions_and_object_motion_are_not_joined():
    doc = person_memory(event('a',52000,56000,action='pick_up'),event('b',56000,60000,action='pick_up'))
    assert len(build_event_groups(doc,DONE)) == 2
    doc = memory(event('a',52000,56000,action='move'),event('b',56000,60000,action='move'))
    assert len(build_event_groups(doc,DONE)) == 2


def test_missing_location_reference_does_not_split_same_person_activity():
    doc = person_memory(event('a',52000,56000,action='walk',target=None),
                        event('b',56000,60000,action='walk',target='table'))
    assert build_event_groups(doc,DONE)[0]['event_ids'] == ['a','b']
    # A missing reference must not bridge two explicitly different locations.
    doc = person_memory(event('a',52000,56000,action='walk',target=None),
                        event('b',56000,60000,action='walk',target='table'),
                        event('c',60000,64000,action='walk',target='bag'))
    assert [g['event_ids'] for g in build_event_groups(doc,DONE)] == [['a','b'],['c']]


def test_repeated_observations_keep_all_sources_and_leave_document_unchanged():
    doc = memory(event('a', 0, 2000), event('b', 28000, 30000), event('c', 56000, 58000))
    before = copy.deepcopy(doc)
    groups = build_event_groups(doc, DONE)
    assert len(groups) == 1 and groups[0]['kind'] == 'repeated_observations'
    assert groups[0]['event_ids'] == ['a', 'b', 'c']
    assert groups[0]['evidence_ids'] == ['ev_a', 'ev_b', 'ev_c']
    assert doc == before


@pytest.mark.parametrize('action,kind,target', [('pick_up','observed',None), ('move','observed','bag'),
    ('place_on','observed','table'), ('occluded','observed',None), ('unknown','unknown',None),
    ('put_in','inferred','bag'), ('seen_at','observed','bag')])
def test_intervening_action_location_or_uncertainty_breaks_observation_group(action, kind, target):
    doc = memory(event('a', 0), event('boundary', 1000, action=action, kind=kind, target=target), event('b', 2000))
    groups = build_event_groups(doc, DONE)
    assert len(groups) == 3
    assert all(len(g['event_ids']) == 1 for g in groups)


@pytest.mark.parametrize('boundary', ['human', 'inferred', 'identity', 'different_id', 'long_gap', 'coverage_gap'])
def test_review_identity_epistemic_and_coverage_boundaries(boundary):
    doc = memory(event('a', 0), event('b', 28000)); job = dict(DONE)
    if boundary == 'human': doc['events'][1]['review_status'] = 'human_reviewed'
    if boundary == 'inferred': doc['events'][1].update(epistemic_type='inferred', rule_id='visual_context_v1')
    if boundary == 'identity': doc['entities'][0]['identity_status'] = 'ambiguous'
    if boundary == 'different_id':
        doc['entities'][-1]['label'] = doc['entities'][0]['label']
        doc['events'][1]['subject_id'] = 'other'
    if boundary == 'long_gap': doc = memory(event('a', 0), event('b', 61000))
    if boundary == 'coverage_gap': job.update(status='partial', analyzed_ranges=[{'start_ms':0,'end_ms':1000}, {'start_ms':27000,'end_ms':30000}])
    assert len(build_event_groups(doc, job)) == 2


def test_overlapping_actions_need_shared_actual_evidence_not_only_close_times():
    a = event('a', 1000, 3000, action='pick_up', frames=[1000, 2000, 3000])
    b = event('b', 2000, 4000, action='pick_up', frames=[2000, 3000, 4000])
    groups = build_event_groups(memory(a, b), DONE)
    assert len(groups) == 1 and groups[0]['kind'] == 'overlapping_actions'
    assert groups[0]['evidence_ids'] == ['ev_a', 'ev_b']
    a['_times'] = [1000, 3000]; b['_times'] = [2000, 4000]
    assert len(build_event_groups(memory(a, b), DONE)) == 2


def test_real_repeated_actions_and_transitive_chains_are_preserved():
    a = event('a', 0, 4000, action='pick_up', frames=[0, 3000, 4000])
    b = event('b', 2000, action='place_on')
    c = event('c', 3000, 5000, action='pick_up', frames=[3000, 4000, 5000])
    assert len(build_event_groups(memory(a, b, c), DONE)) == 3
    chain = memory(event('a', 0, 2000, action='pick_up'), event('b', 2000, 4000, action='pick_up'),
                   event('c', 4000, 6000, action='pick_up'))
    assert [g['event_ids'] for g in build_event_groups(chain, DONE)] == [['a', 'b'], ['c']]


def test_target_motion_and_long_overlapping_motion_break_groups():
    doc = memory(event('a', 0), event('moving_table', 1000, action='move', target=None), event('b', 2000))
    doc['events'][1]['subject_id'] = 'table'
    assert len(build_event_groups(doc, DONE)) == 3
    doc = memory(event('motion', 0, 10000, action='move'), event('a', 1000), event('b', 2000))
    assert len(build_event_groups(doc, DONE)) == 3


def test_history_is_compact_but_last_seen_and_cutoff_remain_precise():
    doc = memory(event('a', 0), event('b', 28000), event('c', 56000))
    result = query_memory(doc, DONE, 'TEST_item的移动经过是什么？', entity_id='item')
    assert len(result['observations']) == 1
    assert result['observations'][0]['event_ids'] == ['a','b','c']
    assert '不表示期间始终在场' in result['answer']
    assert {e['id'] for e in result['evidence']} == {'ev_a','ev_b','ev_c'}
    result = query_memory(doc, DONE, '最后在哪里？', entity_id='item')
    assert result['observations'][0]['event_ids'] == ['c']
    assert result['observations'][0]['evidence_ids'] == ['ev_c']
    result = query_memory(doc, DONE, '移动经过是什么？', as_of_ms=30000, entity_id='item')
    assert result['observations'][0]['event_ids'] == ['a','b']
    assert all(e['end_ms'] <= 30000 for e in result['evidence'])


def test_api_grouping_does_not_modify_storage_and_human_review_is_individual(client, analyzed):
    vid, _ = analyzed
    db = client.app.state.db
    doc, job = db.raw_memory(vid)
    first = doc['events'][0]
    doc['events'] = []
    for i, frame in enumerate(doc['frames'][:3]):
        ref = {'id':f'ev_repeat_{i}', 'video_id':vid, 'start_ms':frame['pts_ms'], 'end_ms':frame['pts_ms'], 'frame_ids':[frame['id']]}
        doc['evidence'].append(ref)
        doc['events'].append({**first,'id':f'repeat_{i}','action':'seen_at','start_ms':frame['pts_ms'], 'end_ms':frame['pts_ms'], 'evidence_ids':[ref['id']]})
    db.save_memory(job['id'], doc)
    response = client.get(f'/api/videos/{vid}/events?grouped=true').json()
    assert len(response['event_groups']) == 1 and len(response['events']) == 3
    assert db.raw_memory(vid)[0] == doc
    state = client.get(f'/api/videos/{vid}/review').json()
    saved = client.post(f'/api/videos/{vid}/review/repeat_1/human', json={
        'run_id':job['id'],'expected_revision':state['revision'],'decision':'confirm','note':'Synthetic isolated test confirmation.'})
    assert saved.status_code == 200
    assert sum(e['human'] is not None for e in saved.json()['entries']) == 1
    response = client.get(f'/api/videos/{vid}/events?grouped=true').json()
    assert len(response['event_groups']) == 3
    assert response['events'][1]['review_status'] == 'human_reviewed'
    assert db.raw_memory(vid)[0] == doc


def test_job_document_retains_overlapping_candidates_and_their_evidence(client, analyzed):
    vid, _ = analyzed
    db, manager = client.app.state.db, client.app.state.manager
    doc, _ = db.raw_memory(vid)
    duplicate = {**doc['events'][0], 'id':'synthetic_duplicate_candidate'}
    result = manager.document(db.video(vid), doc['frames'], {e['id']:e for e in doc['entities']},
                              doc['evidence'], [*doc['events'], duplicate])
    assert len(result['events']) == len(doc['events'])+1
    assert result['evidence'] == doc['evidence']


@pytest.mark.parametrize('person,action', [(True, 'fall'), (False, 'pick_up'), (False, 'put_in')])
def test_duplicates_merge_across_supporting_sightings(person, action):
    factory = person_memory if person else memory
    doc = factory(event('a', 5000, 7000, action=action, frames=[5000,6000,7000]),
                  event('sighting', 6000),
                  event('b', 6000, 7000, action=action, frames=[6000,7000]))
    if person:
        # Floor posture is a separate fact, not a second fall or a reset.
        posture = event('posture',6000,7000,action='lying_on_floor')
        doc = factory(event('leading_walk',3000,5000,action='walk'),
                      event('a',5000,7000,action=action,frames=[5000,6000,7000]),
                      event('sighting',6000),posture,event('b',6000,7000,action=action))
    before = copy.deepcopy(doc)
    group = next(g for g in build_event_groups(doc,DONE) if 'a' in g['event_ids'])
    assert group['event_ids'] == ['a','b'] and group['kind'] == 'overlapping_actions'
    assert group['evidence_ids'] == ['ev_a','ev_b']
    assert (group['start_ms'],group['end_ms']) == (5000,7000)
    assert doc == before
    result = query_memory(doc,DONE,'移动经过是什么？',entity_id='item')
    assert sum('a' in c['event_ids'] for c in result['observations']) == 1
    assert next(c for c in result['observations'] if 'a' in c['event_ids'])['event_ids'] == ['a','b']


@pytest.mark.parametrize('boundary', ['stand_up','walk','unknown','different_subject','different_target',
                                     'no_shared_frame','human'])
def test_fall_duplicate_matching_keeps_real_boundaries(boundary):
    a = event('a',5000,9000,action='fall',frames=[5000,7000,9000])
    b = event('b',7000,10000,action='fall',frames=[7000,9000,10000])
    middle = event('middle',6000,action='seen_at')
    if boundary in {'stand_up','walk','unknown'}: middle['action'] = boundary
    if boundary == 'different_subject': b['subject_id'] = 'other'
    if boundary == 'different_target': b['target_id'] = 'bag'
    if boundary == 'no_shared_frame': b['_times'] = [8000,10000]
    if boundary == 'human': b['review_status'] = 'human_reviewed'
    groups = build_event_groups(person_memory(a,middle,b),DONE)
    assert not any({'a','b'} <= set(g['event_ids']) for g in groups)


@pytest.mark.parametrize('inferred_first', [False, True])
def test_same_fall_merges_observed_and_inferred_optional_location(inferred_first):
    a = event('a',2750,3500,action='fall',target=None,frames=[2750,3000,3250,3500])
    b = event('b',3000,3750,action='fall',kind='inferred',frames=[3000,3250,3500,3750])
    if inferred_first:
        a.update(epistemic_type='inferred',rule_id='visual_context_v1',uncertainty_reasons=['Synthetic test uncertainty'])
        b.update(epistemic_type='observed',rule_id=None,uncertainty_reasons=[])
    doc = person_memory(a,event('posture',3500,3750,action='lying_on_floor'),b)
    before = copy.deepcopy(doc)
    group = next(g for g in build_event_groups(doc,DONE) if 'a' in g['event_ids'])
    assert group['event_ids'] == ['a','b'] and group['kind'] == 'overlapping_actions'
    assert group['evidence_ids'] == ['ev_a','ev_b']
    assert (group['start_ms'],group['end_ms']) == (2750,3750)
    assert doc == before


def test_missing_location_does_not_bridge_conflicting_fall_locations():
    doc = person_memory(event('a',0,4000,action='fall',target=None,frames=[0,2000,4000]),
                        event('b',1000,5000,action='fall',target='table',kind='inferred',frames=[1000,2000,4000]),
                        event('c',2000,6000,action='fall',target='bag',frames=[2000,4000,6000]))
    assert not any({'a','b','c'} <= set(g['event_ids']) for g in build_event_groups(doc,DONE))


def test_qa_merges_mixed_fall_without_promoting_inference():
    doc = person_memory(event('a',2750,3500,action='fall',target=None,frames=[2750,3000,3250,3500]),
                        event('b',3000,3750,action='fall',kind='inferred',frames=[3000,3250,3500,3750]))
    result = query_memory(doc,DONE,'视频中什么时候有人跌倒？')
    assert not result['observations'] and len(result['inferences']) == 1
    assert result['inferences'][0]['event_ids'] == ['a','b']
    assert result['inferences'][0]['evidence_ids'] == ['ev_a','ev_b']
    assert '同一次事件' in result['inferences'][0]['text']


def test_duplicate_matching_does_not_chain_shared_evidence():
    doc = memory(event('a',0,4000,action='pick_up',frames=[0,1000]),
                 event('b',1000,5000,action='pick_up',frames=[1000,3000]),
                 event('c',2000,6000,action='pick_up',frames=[3000,6000]))
    assert [g['event_ids'] for g in build_event_groups(doc,DONE)] == [['a','b'],['c']]

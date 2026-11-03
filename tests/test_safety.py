"""Synthetic person/events and synthetic video only; no real safety-performance claims."""
import copy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.errors import AppError
from backend.events import Extraction, normalize, validate_memory
from backend.main import create_app
from backend.providers.base import ProviderResult
from backend.safety import derive_alerts
from tests.conftest import wait_job


def document(action='fall', score=.9, kind='observed', end=12000):
    frames = [{'id': 'f0', 'pts_ms': 0, 'kind': 'original_frame'},
              {'id': 'f1', 'pts_ms': end, 'kind': 'original_frame'}]
    extraction = Extraction.model_validate({'entities': [{'id': 'p', 'label': '人物1（白色上衣）', 'entity_type': 'person'}],
        'events': [{'subject_id': 'p', 'action': action, 'frame_ids': ['f0', 'f1'], 'model_score': score,
                    'epistemic_type': kind, 'uncertainty_reasons': ['局部过程被遮挡'] if kind == 'inferred' else []}]})
    entities, evidence, events = normalize(extraction, 'v_TEST_PERSON', {'start_ms': 0, 'frames': frames}, {})
    return {'schema_version': '0.1', 'fixture_kind': 'manual_design_example', 'video_id': 'v_TEST_PERSON',
            'video_duration_ms': end+1000, 'entities': list(entities.values()), 'frames': frames, 'events': events, 'evidence': evidence}


@pytest.mark.parametrize('action', ['walk', 'sit_down', 'stand_up', 'lie_down', 'lying_on_floor', 'fall', 'climb', 'help_gesture'])
def test_person_actions_round_trip_schema_and_evidence(action):
    memory = document(action)
    validate_memory(memory)
    assert memory['entities'][0]['entity_type'] == 'person'


@pytest.mark.parametrize('fault', ['wrong_subject', 'one_frame'])
def test_person_action_requires_person_and_multiple_times(fault):
    raw = Extraction.model_validate({'entities': [{'id': 'p', 'label': 'TEST', 'entity_type': 'object' if fault == 'wrong_subject' else 'person'}],
        'events': [{'subject_id': 'p', 'action': 'fall', 'frame_ids': ['f0']}]})
    with pytest.raises(AppError):
        normalize(raw, 'v_TEST', {'start_ms': 0, 'frames': [{'id': 'f0', 'pts_ms': 0}]}, {})


@pytest.mark.parametrize('score,kind,expected', [(.9,'observed','urgent'),(.7,'observed','urgent'),
                                               (None,'observed','urgent'),(.9,'inferred','urgent'),(.1,'observed',None)])
def test_alert_urgency_keeps_inference_and_score_boundaries(score, kind, expected):
    alerts = derive_alerts(document(score=score, kind=kind), 'test-run')
    assert (alerts[0]['severity'] if alerts else None) == expected
    if alerts:
        assert alerts[0]['evidence_ids'] and alerts[0]['source'] == 'uploaded_recording' and alerts[0]['is_mock']


def test_floor_lying_duration_normal_activity_and_bad_evidence():
    assert not derive_alerts(document('lying_on_floor', end=9000), 'test-run')
    assert derive_alerts(document('lying_on_floor', end=10000), 'test-run')[0]['severity'] == 'attention'
    for action in ['walk', 'sit_down', 'stand_up', 'lie_down']:
        assert not derive_alerts(document(action), 'test-run')
    for fault in ['missing', 'non_original', 'ambiguous']:
        memory = document()
        if fault == 'missing': memory['evidence'] = []
        elif fault == 'non_original': memory['frames'][0]['kind'] = 'manual_placeholder'
        else: memory['entities'][0]['identity_status'] = 'ambiguous'
        alerts = derive_alerts(memory, 'test-run')
        assert not alerts or (fault == 'ambiguous' and alerts[0]['severity'] == 'urgent')


def test_overlapping_alerts_share_one_stable_episode():
    memory = document()
    second = {**copy.deepcopy(memory['events'][0]), 'id': 'e_SECOND'}
    memory['events'].append(second)
    alerts = derive_alerts(memory, 'test-run')
    assert len(alerts) == 1 and len(alerts[0]['event_ids']) == 2
    assert derive_alerts(memory, 'test-run')[0]['id'] == alerts[0]['id']


def test_fall_alert_dedup_survives_posture_but_respects_recovery():
    from tests.test_grouping import person_memory, event, DONE
    memory = person_memory(event('a',5000,9000,action='fall',frames=[5000,7000,9000]),
                           event('sighting',6000),event('posture',6000,9000,action='lying_on_floor'),
                           event('b',7000,10000,action='fall',frames=[7000,9000,10000]))
    for frame in memory['frames']: frame['kind'] = 'original_frame'
    alerts = derive_alerts(memory,'test-run',DONE)
    assert len(alerts) == 1 and alerts[0]['event_ids'] == ['a','b']
    assert alerts[0]['evidence_ids'] == ['ev_a','ev_b']
    memory['events'][1]['action'] = 'stand_up'
    assert len(derive_alerts(memory,'test-run',DONE)) == 2


def test_mixed_fall_sources_are_one_cautious_alert_with_all_evidence():
    from tests.test_grouping import person_memory, event, DONE
    memory = person_memory(event('a',2750,3500,action='fall',target=None,frames=[2750,3000,3250,3500]),
                           event('b',3000,3750,action='fall',kind='inferred',frames=[3000,3250,3500,3750]))
    for frame in memory['frames']: frame['kind'] = 'original_frame'
    alerts = derive_alerts(memory,'test-run',DONE)
    assert len(alerts) == 1 and alerts[0]['epistemic_type'] == 'inferred'
    assert alerts[0]['event_ids'] == ['a','b'] and alerts[0]['evidence_ids'] == ['ev_a','ev_b']


def test_refresh_derived_alerts_keeps_acknowledgement_and_raw_memory(client, analyzed):
    import json
    vid, _ = analyzed
    db = client.app.state.db
    doc, job = db.raw_memory(vid)
    source = doc['events'][0]
    source.update(action='fall',target_id=None,model_score=.9)
    doc['entities'][0]['entity_type'] = 'person'
    source['subject_id'] = doc['entities'][0]['id']
    frames = doc['frames'][:3]
    source.update(start_ms=frames[0]['pts_ms'],end_ms=frames[-1]['pts_ms'])
    ref = doc['evidence'][0]
    ref.update(start_ms=source['start_ms'],end_ms=source['end_ms'],frame_ids=[f['id'] for f in frames])
    source['evidence_ids'] = [ref['id']]
    doc['events'] = [source,{**copy.deepcopy(source),'id':source['id']+'_duplicate'}]
    db.save_memory(job['id'],doc)
    alerts = db.safety_alerts(vid)
    assert len(alerts) == 1
    # Simulate an older separately stored, acknowledged duplicate alert.
    legacy = {**alerts[0],'id':'legacy_duplicate','event_ids':[doc['events'][1]['id']]}
    with db.connect() as c:
        c.execute('INSERT INTO safety_alerts VALUES(?,?,?,?,?)',
                  (legacy['id'],vid,job['id'],1,json.dumps(legacy)))
    before = copy.deepcopy(db.raw_memory(vid)[0])
    db.refresh_active_alerts()
    after = db.safety_alerts(vid)
    assert len(after) == 1 and after[0]['acknowledged']
    assert db.raw_memory(vid)[0] == before


def absence_document():
    memory = document('walk', end=1000)
    memory['entities'].append({'id': 'scene', 'label': '摄像头画面', 'entity_type': 'location',
                               'aliases': [], 'appearance': '', 'identity_status': 'distinct_candidate'})
    for i in range(10):
        start, end = 2000+i*30000, 32000+i*30000
        memory['frames'].extend([{'id': f'n{i}a', 'pts_ms': start, 'kind': 'original_frame'},
                                  {'id': f'n{i}b', 'pts_ms': end, 'kind': 'original_frame'}])
        memory['evidence'].append({'id': f'ne{i}', 'video_id': memory['video_id'], 'start_ms': start, 'end_ms': end,
                                   'frame_ids': [f'n{i}a', f'n{i}b']})
        memory['events'].append({'id': f'negative{i}', 'video_id': memory['video_id'], 'subject_id': 'scene', 'target_id': None,
                                  'start_ms': start, 'end_ms': end, 'action': 'no_person_visible', 'epistemic_type': 'observed',
                                  'model_score': .9, 'review_status': 'model_unreviewed', 'evidence_ids': [f'ne{i}'],
                                  'source_event_ids': [], 'rule_id': None, 'uncertainty_reasons': []})
    memory['video_duration_ms'] = 303000
    return memory, {'analyzed_ranges': [{'start_ms': 0, 'end_ms': 303000}]}


def test_absence_requires_five_minutes_and_keeps_evidence():
    memory, job = absence_document()
    validate_memory(memory)
    alerts = derive_alerts(memory, 'test-run', job)
    assert len(alerts) == 1 and alerts[0]['action'] == 'long_absence' and alerts[0]['severity'] == 'urgent'
    assert len(alerts[0]['evidence_ids']) == 10 and alerts[0]['epistemic_type'] == 'inferred'
    assert not derive_alerts(memory, 'test-run', job, absence_seconds=301)
    assert not derive_alerts(memory, 'test-run')  # No analyzed coverage is never presumed complete.


@pytest.mark.parametrize('fault', ['no_prior_person', 'analysis_gap', 'person_returns', 'unknown', 'weak_score', 'missing_evidence'])
def test_absence_is_not_inferred_from_missing_or_conflicting_information(fault):
    memory, job = absence_document()
    if fault == 'no_prior_person': memory['events'].pop(0)
    elif fault == 'analysis_gap': job['analyzed_ranges'] = [{'start_ms': 0, 'end_ms': 122000}, {'start_ms': 152000, 'end_ms': 303000}]
    elif fault == 'person_returns':
        returning = copy.deepcopy(memory['events'][0]); returning.update(id='returning', start_ms=152000, end_ms=153000)
        memory['events'].append(returning)
    elif fault == 'unknown': memory['events'][5]['epistemic_type'] = 'unknown'
    elif fault == 'weak_score': memory['events'][5]['model_score'] = .6
    else: memory['events'][5]['evidence_ids'] = ['MISSING']
    assert not derive_alerts(memory, 'test-run', job)


class SafetyMock:
    name, model, is_mock = 'TEST_MOCK', 'SYNTHETIC_PERSON_NOT_AI', True
    def ready(self): pass
    def extract(self, *, window, entities, **kwargs):
        person = next((e['id'] for e in entities if e.get('entity_type') == 'person'), 'person')
        data = {'entities': [] if entities else [{'id': person, 'label': '人物1（测试）', 'entity_type': 'person'}],
                'events': [{'subject_id': person, 'action': 'fall' if window['start_ms'] == 0 else 'walk',
                            'model_score': .95, 'frame_ids': [window['frames'][0]['id'], window['frames'][-1]['id']]}]}
        return ProviderResult(Extraction.model_validate(data), {'test_mock': True})


def test_video_alert_acknowledge_restart_query_and_delete(settings, sample_video):
    with TestClient(create_app(settings, SafetyMock()), base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status'] == 'completed', job
        report = client.get(f'/api/videos/{vid}/alerts').json()
        assert not report['live_camera_connected'] and not report['remote_notifications_configured']
        assert len(report['alerts']) == 1 and len(report['frames']) == 2
        alert = report['alerts'][0]
        assert client.get(report['frames'][0]['url']).status_code == 200
        result = client.post(f'/api/videos/{vid}/query', json={'question': '人物1什么时候跌倒？'}).json()
        assert result['observations'] and result['evidence'] and '疑似跌倒' in result['answer']
        assert client.post(f"/api/videos/{vid}/alerts/{alert['id']}/acknowledge").status_code == 200
        raw, _ = client.app.state.db.raw_memory(vid)
        client.app.state.db.save_memory(job['id'], raw)
        assert client.get(f'/api/videos/{vid}/alerts').json()['alerts'][0]['acknowledged']
        other = client.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        assert client.post(f"/api/videos/{other}/alerts/{alert['id']}/acknowledge").status_code == 404
        assert client.app.state.db.usage_summary()['requests'] == 0
    with TestClient(create_app(settings, SafetyMock()), base_url='http://localhost') as client:
        assert client.get(f'/api/videos/{vid}/alerts').json()['alerts'][0]['acknowledged']
        assert client.delete(f'/api/videos/{vid}').status_code == 200
        with client.app.state.db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM safety_alerts WHERE video_id=?', (vid,)).fetchone()[0] == 0

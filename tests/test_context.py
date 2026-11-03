"""Explicit synthetic fixtures; no actual visual-model results or paid requests here."""
import copy
import json

from fastapi.testclient import TestClient
import pytest

from backend.answer import query_memory
from backend.errors import AppError
from backend.events import Extraction, normalize, validate_memory
from backend.main import create_app
from backend.state import replay
from tests.test_review import ReviewMock, upload_and_analyze


def test_context_inference_is_evidence_backed_but_never_a_direct_state(client, analyzed):
    vid, _ = analyzed
    doc, job = client.app.state.db.raw_memory(vid)
    known = {e['id']: e for e in doc['entities']}
    original = doc['events'][1]
    window = {'start_ms': 0, 'frames': doc['frames'][:4]}
    raw = Extraction.model_validate({'entities': [], 'events': [{
        'subject_id': original['subject_id'], 'target_id': original['target_id'], 'action': 'put_in',
        'epistemic_type': 'inferred', 'frame_ids': [f['id'] for f in window['frames'][2:]],
        'uncertainty_reasons': ['Synthetic assumption only: before/after change; crossing not visible.']}]})
    _, evidence, events = normalize(raw, vid, window, known)
    doc.update(events=events, evidence=evidence)
    validate_memory(doc)
    state = replay(doc, doc['video_duration_ms'])[original['subject_id']]
    assert state.last_direct is None and state.container_event is None
    result = query_memory(doc, job, '钥匙可能放入背包了吗？')
    assert result['status'] == 'partial' and result['observations'] == []
    assert result['inferences'][0]['rule_id'] == 'visual_context_v1'
    assert result['evidence'] == evidence and '可能' in result['answer']
    assert query_memory(doc, job, '钥匙可能放入背包了吗？', as_of_ms=0)['inferences'] == []
    overlap = copy.deepcopy(doc)
    direct = {**original, 'action': 'seen_at'}
    overlap['evidence'].extend(client.app.state.db.raw_memory(vid)[0]['evidence'])
    overlap['events'].append(direct)
    # A competing hypothesis must not turn an otherwise consistent direct observation ambiguous.
    state = replay(overlap, overlap['video_duration_ms'])[original['subject_id']]
    assert not state.ambiguous and state.last_direct['id'] == direct['id']
    assert state.container_event is None
    for fault in ['no_reason', 'one_time', 'foreign_frame']:
        bad = raw.model_copy(deep=True)
        if fault == 'no_reason': bad.events[0].uncertainty_reasons = []
        if fault == 'one_time': bad.events[0].frame_ids = [window['frames'][0]['id']]
        if fault == 'foreign_frame': bad.events[0].frame_ids = ['forged', window['frames'][0]['id']]
        with pytest.raises(AppError): normalize(bad, vid, window, known)
    broken = copy.deepcopy(doc)
    broken['events'][0]['rule_id'] = 'conditional_containment_v1'
    with pytest.raises(AppError): validate_memory(broken)  # Derived rules still need source events.


class ContextMock(ReviewMock):
    def verify_event(self, *, frames, instructions, **kwargs):
        if '本次同时允许上下文推测' not in instructions:
            return super().verify_event(frames=frames, instructions=instructions, **kwargs)
        return json.dumps({'verdict': 'uncertain', 'reason': 'Synthetic test: action not fully visible.',
                           'frame_ids': [f['id'] for f in frames[:2]], 'motion_visible': False,
                           'post_state_visible': True, 'hypothesis': {
                               'action': 'move', 'target_id': None, 'frame_ids': [f['id'] for f in frames[:2]],
                               'reason': 'Synthetic test: before/after positions differ, movement inferred; occlusion remains an alternative.'}}), {'test_mock': True}


def test_context_review_updates_timeline_qa_and_replay_preserving_human_priority(settings, sample_video):
    with TestClient(create_app(settings, ContextMock()), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        original, _ = client.app.state.db.raw_memory(vid)
        event = original['events'][1]
        body = {'run_id': job['id'], 'expected_revision': 0, 'mode': 'context'}
        response = client.post(f'/api/videos/{vid}/review/{event["id"]}/verify', json=body)
        assert response.status_code == 200, response.text
        entry = next(e for e in response.json()['entries'] if e['original']['id'] == event['id'])
        assert entry['status'] == 'ai_context' and entry['machine']['is_mock'] is True
        effective = client.get(f'/api/videos/{vid}/events').json()
        after = next(e for e in effective['events'] if e['id'] == event['id'])
        assert after['action'] == 'move' and after['epistemic_type'] == 'inferred'
        assert after['review_status'] == 'model_unreviewed'
        answer = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙的经过是什么？'}).json()
        assert any(c['rule_id'] == 'visual_context_v1' for c in answer['inferences'])
        chain = client.get(f'/api/videos/{vid}/replay', params={'entity_id': event['subject_id']}).json()
        assert any(e['rule_id'] == 'visual_context_v1' for e in chain['entries'])
        assert client.app.state.db.raw_memory(vid)[0] == original
        revision = response.json()['revision']
        human = client.post(f'/api/videos/{vid}/review/{event["id"]}/human', json={
            'run_id': job['id'], 'expected_revision': revision, 'decision': 'reject', 'note': 'Synthetic rejection only.'})
        assert human.status_code == 200
        body['expected_revision'] = human.json()['revision']
        assert client.post(f'/api/videos/{vid}/review/{event["id"]}/verify', json=body).status_code == 200
        effective = client.get(f'/api/videos/{vid}/events').json()
        assert next(e for e in effective['events'] if e['id'] == event['id'])['epistemic_type'] == 'unknown'


class ContradictingContextMock(ReviewMock):
    def verify_event(self, *, frames, instructions, **kwargs):
        if '本次同时允许上下文推测' not in instructions:
            return super().verify_event(frames=frames, instructions=instructions, **kwargs)
        subject = json.loads(instructions.split('\n')[1])
        return json.dumps({'verdict': 'uncertain', 'reason': 'Synthetic contradictory guess.',
                           'frame_ids': [f['id'] for f in frames[:2]], 'motion_visible': False,
                           'post_state_visible': False, 'hypothesis': {'action': 'put_in',
                               'target_id': subject['target']['id'], 'frame_ids': [f['id'] for f in frames[:2]],
                               'reason': 'Synthetic attempt to revive a refuted action.'}}), {'test_mock': True}


def test_context_cannot_revive_previously_refuted_action(settings, sample_video):
    with TestClient(create_app(settings, ContradictingContextMock()), base_url='http://localhost') as client:
        vid, job = upload_and_analyze(client, sample_video)
        doc, _ = client.app.state.db.raw_memory(vid)
        event = doc['events'][1]
        url = f'/api/videos/{vid}/review/{event["id"]}/verify'
        strict = client.post(url, json={'run_id': job['id'], 'expected_revision': 0})
        assert strict.status_code == 200
        context = client.post(url, json={'run_id': job['id'], 'expected_revision': strict.json()['revision'], 'mode': 'context'})
        assert context.status_code == 422 and context.json()['error']['code'] == 'CONTEXT_CONTRADICTION'
        effective = client.get(f'/api/videos/{vid}/events').json()
        assert next(e for e in effective['events'] if e['id'] == event['id'])['action'] == 'unknown'

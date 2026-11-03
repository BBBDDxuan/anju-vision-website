"""Synthetic tests for routing, replay boundaries, request budgets and Unicode paths."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.db import Database
from backend.errors import AppError
from backend.media import extract_frames
from backend.replay import build_replay


def test_replay_links_container_without_claiming_direct_visibility(client, analyzed):
    vid, doc = analyzed
    key = doc['events'][1]['subject_id']
    result = client.get(f'/api/videos/{vid}/replay', params={'entity_id': key}).json()
    assert [e['start_ms'] for e in result['entries']] == sorted(e['start_ms'] for e in result['entries'])
    inferred = [e for e in result['entries'] if e['epistemic_type'] == 'inferred']
    assert len(inferred) == 2 and all(len(e['source_event_ids']) == 2 for e in inferred)
    assert result['unknowns'] and result['is_mock']
    early = client.get(f'/api/videos/{vid}/replay', params={'entity_id': key, 'as_of_ms': 1000}).json()
    assert all(e['end_ms'] <= 1000 for e in early['entries'])
    assert client.get(f'/api/videos/{vid}/replay', params={'entity_id': 'forged'}).status_code == 404


def test_replay_take_out_ends_container_interval(analyzed):
    _, doc = analyzed
    entry = copy.deepcopy(doc['events'][1])
    entry.update(id='test_takeout', action='take_out', start_ms=4000, end_ms=5000)
    doc['events'].append(entry)
    result = build_replay(doc, {'status': 'completed', 'is_mock': True}, entry['subject_id'])
    assert not [e for e in result['entries'] if e['epistemic_type'] == 'inferred']


def test_visual_inference_keeps_reasons_separate_from_condition(analyzed):
    _, doc = analyzed
    event = doc['events'][0]
    reasons = ['看到的前后变化：物品重新出现；推测的动作：放回；没拍清的部分：手部遮挡。']
    event.update(epistemic_type='inferred', rule_id='visual_context_v1', uncertainty_reasons=reasons)
    result = build_replay(doc, {'status': 'completed', 'is_mock': True}, event['subject_id'])
    entry = next(e for e in result['entries'] if e['id'] == event['id'])
    assert 'condition' not in entry
    assert entry['event']['uncertainty_reasons'] == reasons
    assert entry['evidence_ids'] == event['evidence_ids']
    assert entry['epistemic_type'] == 'inferred'


def test_action_query_does_not_answer_different_action(client, analyzed):
    vid, doc = analyzed
    result = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙什么时候被拿起？'}).json()
    assert not result['observations'] and result['status'] == 'unknown'
    result = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙被放进了什么地方？'}).json()
    assert result['observations'] and all('放入' in c['text'] for c in result['observations'])
    # A longer container alias later in the sentence must not become the queried subject.
    result = client.post(f'/api/videos/{vid}/query', json={'question': '钥匙现在还在黑色背包里吗？'}).json()
    assert '钥匙被放入' in result['answer'] and result['unknowns']
    key = doc['events'][1]['subject_id']
    result = client.post(f'/api/videos/{vid}/query', json={'question': '这个物品最后在哪里出现？', 'entity_id': key}).json()
    assert result['observations']


def test_request_cap_counts_failed_reservations_atomically(settings):
    db = Database(settings.data_dir)
    def reserve(_):
        try:
            request_id = db.reserve_request('v', 'j', 1, 1)
            db.finish_request(request_id, 'failed', {'usage': {}})
            return True
        except AppError:
            return False
    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(reserve, range(4))) == 1
    assert db.usage_summary()['requests'] == 1


def test_unicode_keyframe_directory(settings, sample_video):
    frames = extract_frames({'original_path': str(sample_video), 'duration_ms': 12000}, settings.data_dir / '中文证据目录', settings)
    from pathlib import Path
    assert len(frames) == 12 and all(Path(f['path']).read_bytes().startswith(b'\xff\xd8') for f in frames)


def test_states_persisted_in_sqlite(client, analyzed):
    _, doc = analyzed
    with client.app.state.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM states WHERE run_id=?', (doc['job']['id'],)).fetchone()[0] == len(doc['entities'])

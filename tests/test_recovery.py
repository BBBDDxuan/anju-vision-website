"""Synthetic fixtures only. Recovery must never create missing observations or repeat saved work."""
from dataclasses import replace
import hashlib
import json

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.errors import AppError
from backend.events import Extraction
from backend.main import create_app
from backend.providers.base import ProviderResult
from backend.providers.formatting import parse_extraction
from backend.providers.qwen import QwenProvider
from backend.db import Database
from tests.conftest import MockVisionProvider, wait_job
from tests.test_provider import arguments, configured, response


def test_lossless_aliases_preserve_claim_and_evidence():
    raw = {'entities': [{'id': 'a', 'name': 'SYNTHETIC_ITEM'}], 'events': [
        {'id': 'model_id', 'subject_id': 'a', 'action': 'seen_at', 'frame_id': 'f0', 'source_pts_ms': 999}]}
    result, notes = parse_extraction(json.dumps(raw))
    assert result.entities[0].label == 'SYNTHETIC_ITEM' and result.events[0].frame_ids == ['f0']
    assert result.events[0].subject_id == 'a' and result.events[0].action == 'seen_at'
    assert notes and 'name' in raw['entities'][0]  # Input/original response stays unchanged.


@pytest.mark.parametrize('event', [
    {'frame_id': 'f0', 'description': 'SYNTHETIC moving object', 'entity_ids': ['a']},
    {'subject_id': 'a', 'action': 'invented_action', 'frame_ids': ['f0']},
    {'subject_id': 'a', 'action': 'seen_at', 'frame_ids': ['f0'], 'description': 'possibly another object'},
])
def test_format_repair_cannot_invent_or_discard_visual_semantics(event):
    with pytest.raises(ValueError):
        parse_extraction(json.dumps({'entities': [{'id': 'a', 'label': 'TEST'}], 'events': [event]}))
    with pytest.raises(ValueError):
        parse_extraction('{"entities":[')


def test_provider_repairs_locally_once_and_audits_actual_recovery_prompt(settings, tmp_path):
    payloads = []
    raw = {'entities': [{'id': 'a', 'name': 'TEST'}], 'events': [{'subject_id': 'a', 'action': 'seen_at', 'frame_id': 'f0'}]}
    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=response(json.dumps(raw)))
    provider = QwenProvider(configured(settings), Database(settings.data_dir), httpx.MockTransport(handler))
    result = provider.extract(**arguments(tmp_path), recovery=True)
    assert len(payloads) == 1 and result.usage['format_repairs']
    assert result.usage['prompt_version'].endswith('+recovery-v1.1')
    assert result.usage['prompt_sha256'] != provider.event_prompt.sha256
    assert '失败窗口' in payloads[0]['messages'][0]['content'][0]['text']
    assert 'entity_type必须明确为person/object/location' in payloads[0]['messages'][0]['content'][0]['text']
    assert list((settings.data_dir/'runs/unit-test-job').glob('format-repair-*.json'))


def test_caption_response_is_retryable_but_never_converted_to_facts(settings,tmp_path):
    raw = json.dumps({'p001':{'label':'SYNTHETIC person collapsing',
                            'description':'Synthetic caption, not structured visual evidence',
                            'start_frame':0,'end_frame':7}})
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200,json=response(raw))
    provider = QwenProvider(configured(settings),Database(settings.data_dir),httpx.MockTransport(handler))
    with pytest.raises(AppError) as caught:
        provider.extract(**arguments(tmp_path))
    assert caught.value.code == 'MODEL_OUTPUT_INVALID' and caught.value.retryable
    assert len(captured) == 1
    records = list((settings.data_dir/'runs/unit-test-job').glob('response-*.json'))
    assert len(records) == 1 and json.loads(records[0].read_text(encoding='utf8'))['response']['choices'][0]['message']['content'] == raw


class FormatRetryMock(MockVisionProvider):
    def __init__(self,always_invalid=False):
        super().__init__()
        self.calls = []
        self.always_invalid = always_invalid
    def extract(self,*,window,recovery=False,**kwargs):
        self.calls.append((window['start_ms'],recovery,[f['id'] for f in window['frames']]))
        if not recovery or self.always_invalid:
            raise AppError('MODEL_OUTPUT_INVALID','Synthetic malformed output.',422,True)
        return super().extract(window=window,**kwargs)


@pytest.mark.parametrize('mode',['success','persistent_invalid','batch_cap'])
def test_format_retry_is_bounded_reobserves_same_frames_and_obeys_budget(settings,sample_video,mode):
    provider = FormatRetryMock(always_invalid=mode == 'persistent_invalid')
    if mode == 'batch_cap':
        settings = replace(settings,max_calls=1)
    else:
        settings = replace(settings,max_output_tokens=16384)
    with TestClient(create_app(settings,provider),base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload',content=sample_video.read_bytes()).json()['video_id']
        job = wait_job(client,client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
        if mode == 'batch_cap':
            assert len(provider.calls) == job['calls'] == 1 and job['status'] == 'paused'
            assert job['completed_windows'] == 0
            return
        # This fixture has two windows. Each gets at most one recovery retry.
        assert len(provider.calls) == job['calls'] == 4
        for first, retry in zip(provider.calls[::2],provider.calls[1::2]):
            assert not first[1] and retry[1]
            assert first[0] == retry[0] and first[2] == retry[2]
        if mode == 'persistent_invalid':
            assert job['status'] == 'failed' and job['completed_windows'] == 0
            assert not client.app.state.db.video(vid)['active_run']
        else:
            assert job['status'] == 'completed' and job['completed_windows'] == 2
            assert len(job['window_recoveries']) == 2
            caches = list((settings.data_dir/'runs'/job['id']).glob('window-*.json'))
            assert all(json.loads(p.read_text(encoding='utf8'))['recovery'] for p in caches)


class SplitMock:
    name, model, is_mock = 'TEST_MOCK', 'SYNTHETIC_RECOVERY_ONLY', True
    def __init__(self): self.calls = []
    def ready(self): pass
    def extract(self, *, window, entities, recovery=False, **kwargs):
        self.calls.append((window['start_ms'], window['end_ms'], recovery))
        if window['end_ms']-window['start_ms'] > 20000:
            raise AppError('MODEL_TRUNCATED' if not recovery else 'MODEL_OUTPUT_INVALID', 'Synthetic injected whole-window failure.')
        entity = entities[0]['id'] if entities else 'test-object'
        return ProviderResult(Extraction.model_validate({'entities': [] if entities else [{'id': entity, 'label': 'SYNTHETIC_ITEM'}],
            'events': [{'subject_id': entity, 'action': 'seen_at', 'frame_ids': [window['frames'][-1]['id']]}]}), {'test_mock': True})


def test_split_budget_restart_does_not_repeat_completed_child(settings, tmp_path):
    source = tmp_path/'SYNTHETIC_RECOVERY_ONLY.mp4'
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (80, 48))
    assert writer.isOpened()
    for i in range(300): writer.write(np.full((48, 80, 3), (i % 255, 40, 30), np.uint8))
    writer.release()
    settings = replace(settings, long_video_threshold_seconds=1)
    provider = SplitMock()
    with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload', content=source.read_bytes()).json()['video_id']
        first = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert first['status'] == 'failed'
        recovering = wait_job(client, client.post(f'/api/videos/{vid}/resume?max_calls=2').json()['id'])
        assert recovering['status'] == 'paused' and recovering['completed_windows'] == 0
        assert recovering['split_windows'] == [0] and recovering['batch_calls'] == 2
        checkpoint = json.loads((settings.data_dir/'runs'/first['id']/'checkpoint.json').read_text(encoding='utf8'))
        assert '0.part0' in checkpoint['unit_processed'] and checkpoint['events']
    with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
        done = wait_job(client, client.post(f'/api/videos/{vid}/resume?max_calls=1').json()['id'])
        assert done['status'] == 'completed' and done['calls'] == 4 and done['completed_windows'] == 1
        assert provider.calls == [(0,30000,False),(0,30000,True),(0,20000,True),(18000,30000,True)]
        memory = client.get(f'/api/videos/{vid}/events').json()
        assert len(memory['events']) == 2 and max(f['pts_ms'] for f in memory['frames']) >= 28000


class FailOnceMock(MockVisionProvider):
    def extract(self, *, window, recovery=False, **kwargs):
        if window['start_ms'] == 6000 and not recovery:
            raise AppError('MODEL_OUTPUT_INVALID', 'Synthetic invalid second-window result.')
        return super().extract(window=window, **kwargs)


def test_successful_window_bytes_and_event_ids_survive_recovery(settings, sample_video):
    with TestClient(create_app(settings, FailOnceMock()), base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['completed_windows'] == 1
        original, _ = client.app.state.db.raw_memory(vid)
        cache = settings.data_dir/'runs'/job['id']/'window-00000.json'
        digest = hashlib.sha256(cache.read_bytes()).hexdigest()
        resumed = wait_job(client, client.post(f'/api/videos/{vid}/resume?max_calls=1').json()['id'])
        assert resumed['status'] == 'completed' and resumed['batch_calls'] == 1
        after, _ = client.app.state.db.raw_memory(vid)
        events = {e['id']: e for e in after['events']}
        assert all(events[e['id']] == e for e in original['events'])
        assert hashlib.sha256(cache.read_bytes()).hexdigest() == digest


def test_free_manual_evidence_never_calls_model_or_marks_human_reviewed(settings, sample_video):
    with TestClient(create_app(settings, MockVisionProvider()), base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        original, _ = client.app.state.db.raw_memory(vid)
        state = client.get(f'/api/videos/{vid}/review').json()
        event_id = original['events'][0]['id']
        response = client.post(f'/api/videos/{vid}/review/{event_id}/sample', json={
            'run_id': job['id'], 'expected_revision': state['revision'], 'center_ms': 6500})
        assert response.status_code == 200, response.text
        result = response.json()
        assert 0 < len(result['manual_frame_ids']) <= 12
        assert client.app.state.db.usage_summary()['requests'] == 0
        assert all(e['human'] is None for e in result['entries'])
        assert client.app.state.db.raw_memory(vid)[0] == original
        for fid in result['manual_frame_ids']:
            assert client.get(f'/media/frames/{vid}/{fid}').status_code == 200
        stale = client.post(f'/api/videos/{vid}/review/{event_id}/sample', json={
            'run_id': 'old-run', 'expected_revision': result['revision'], 'center_ms': 100})
        assert stale.status_code == 409

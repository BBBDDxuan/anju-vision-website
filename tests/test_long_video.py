"""Long-video behavior uses synthetic video and an explicitly mocked provider; no paid requests."""
from dataclasses import replace
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.events import Extraction
from backend.events import normalize
from backend.errors import AppError
from backend.main import create_app
from backend.media import extract_frames, inspect_video
from backend.providers.base import ProviderResult
from tests.conftest import wait_job


class LongVideoMock:
    name, model, is_mock = 'TEST_MOCK', 'long-video-test-only-not-ai', True

    def __init__(self):
        self.windows = []

    def ready(self):
        pass

    def extract(self, *, window, entities, **kwargs):
        self.windows.append(window['start_ms'])
        identifier = entities[0]['id'] if entities else 'TEST_OBJECT'
        return ProviderResult(Extraction.model_validate({'entities': [] if entities else [
            {'id': identifier, 'label': '测试物品（非AI识别）'}], 'events': [
            {'subject_id': identifier, 'action': 'seen_at', 'frame_ids': [window['frames'][-1]['id']]}]}), {'test_mock': True})


def test_resumable_batch_does_not_repeat_windows_or_extract_frames(settings, sample_video):
    settings = replace(settings, max_calls=1)
    provider = LongVideoMock()
    with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
        vid = client.post('/api/videos', files={'file': ('test.mp4', sample_video.read_bytes())}).json()['video_id']
        plan = client.get(f'/api/videos/{vid}/analysis-plan').json()
        assert plan['total_windows'] == 2 and plan['max_windows_per_batch'] == 1
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
        assert job['status'] == 'paused' and job['progress'] == 50 and job['resumable']
        first = client.get(f'/api/videos/{vid}/events').json()
        assert len(first['events']) == 1
        result = client.post(f'/api/videos/{vid}/query', json={'question': '测试物品（非AI识别）最后在哪里出现？'}).json()
        assert result['status'] == 'partial' and result['unknowns']
    # Restart and continue from durable checkpoint without sampling or analyzing the first window again.
    with patch('backend.jobs.extract_frames', side_effect=AssertionError('must reuse frames')):
        with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
            response = client.post(f'/api/videos/{vid}/resume')
            assert response.status_code == 202
            resumed = wait_job(client, response.json()['id'])
            assert resumed['id'] == job['id'] and resumed['status'] == 'completed'
            assert resumed['completed_windows'] == 2 and resumed['calls'] == 2 and resumed['batch_calls'] == 1
            memory = client.get(f'/api/videos/{vid}/events').json()
            assert len(memory['events']) == 2 and provider.windows == [0, 6000]
            assert memory['events'][-1]['start_ms'] == 11000
            assert client.post(f'/api/videos/{vid}/resume').status_code == 409


def test_changed_sampling_cannot_resume(settings, sample_video):
    settings = replace(settings, max_calls=1)
    with TestClient(create_app(settings, LongVideoMock()), base_url='http://localhost') as client:
        vid = client.post('/api/videos', files={'file': ('test.mp4', sample_video.read_bytes())}).json()['video_id']
        wait_job(client, client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
    with TestClient(create_app(replace(settings, sampling_fps=.5), LongVideoMock()), base_url='http://localhost') as client:
        response = client.post(f'/api/videos/{vid}/resume')
        assert response.status_code == 409 and response.json()['error']['code'] == 'CHECKPOINT_CONFIG'


def test_batch_output_budget_is_not_divided_by_whole_video(settings, sample_video):
    settings = replace(settings, max_output_tokens=2048, window_output_tokens=2048)
    with TestClient(create_app(settings, LongVideoMock()), base_url='http://localhost') as client:
        vid = client.post('/api/videos', files={'file': ('test.mp4', sample_video.read_bytes())}).json()['video_id']
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
        assert job['status'] == 'paused' and job['batch_output_budget'] == 2048 and job['calls'] == 1
        assert wait_job(client, client.post(f'/api/videos/{vid}/resume').json()['id'])['status'] == 'completed'


def test_more_than_7200_source_frames_and_late_evidence(settings, tmp_path):
    path = tmp_path / 'long-7501-frames.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 30, (80, 48))
    assert writer.isOpened()
    image = np.zeros((48, 80, 3), dtype=np.uint8)
    for index in range(7501):
        image[:] = (index % 255, 40, 30)
        writer.write(image)
    writer.release()
    meta = inspect_video(path, settings)
    assert meta['duration_ms'] > 240000
    frames = extract_frames({'original_path': str(path), **meta}, tmp_path / 'long-frames', settings)
    assert frames[-1]['pts_ms'] >= 249000 and frames[-1]['source_frame_index'] >= 7470
    assert len(frames) in {250, 251}


def test_configurable_upload_limit(client, settings):
    assert client.get('/api/health').json()['limits']['max_video_seconds'] == 86400
    assert client.get('/api/health').json()['limits']['max_upload_mb'] == 65536


def test_sampling_frame_limit_is_explicit(settings, sample_video, tmp_path):
    with pytest.raises(AppError, match='抽样帧数量超过'):
        extract_frames({'original_path': str(sample_video), 'duration_ms': 12000}, tmp_path / 'frames', replace(settings, max_sampled_frames=2))


def test_relation_without_target_is_unknown_not_invented_fact():
    raw = Extraction.model_validate({'entities': [{'id':'a','label':'测试物品'}], 'events':[
        {'subject_id':'a','action':'place_on','target_id':None,'frame_ids':['f0']}]})
    _, _, events = normalize(raw, 'v', {'start_ms':0,'frames':[{'id':'f0','pts_ms':0}]}, {})
    assert events[0]['action']=='unknown' and events[0]['epistemic_type']=='unknown'
    assert events[0]['target_id'] is None and events[0]['uncertainty_reasons']


def test_cached_real_contract_output_does_not_call_model(settings, sample_video):
    settings = replace(settings, max_calls=1)
    provider = LongVideoMock()
    with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
        vid=client.post('/api/videos',files={'file':('test.mp4',sample_video.read_bytes())}).json()['video_id']
        job=wait_job(client,client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
        import json
        run=settings.data_dir/'runs'/job['id']
        checkpoint=json.loads((run/'checkpoint.json').read_text(encoding='utf8'))
        # Simulate a crash after a validated cached output was saved, before the checkpoint was updated.
        checkpoint['processed']=[]; checkpoint['events']=[]; checkpoint['evidence']=[]; checkpoint['entities']={}
        checkpoint['unit_processed']=[]
        (run/'checkpoint.json').write_text(json.dumps(checkpoint),encoding='utf8')
        resumed=wait_job(client,client.post(f'/api/videos/{vid}/resume').json()['id'])
        assert resumed['status']=='completed' and resumed['cached_windows']==1
        assert provider.windows==[0,6000] and resumed['calls']==2

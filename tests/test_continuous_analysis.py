"""Synthetic video / mock vision / HTTP MockTransport only; never calls a paid API."""
from dataclasses import replace
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.db import Database
from backend.errors import AppError
from backend.main import create_app
from backend.providers.qwen import QwenProvider
from tests.conftest import wait_job
from tests.test_long_video import LongVideoMock
from tests.test_provider import response


def upload(client, video):
    result = client.post('/api/videos/upload', content=video.read_bytes())
    assert result.status_code == 201, result.text
    return result.json()['video_id']


@pytest.mark.parametrize('segmented', [False, True])
@pytest.mark.parametrize('budget', [
    {'max_calls': 1},
    {'max_output_tokens': 4096},
    {'max_frame_references': 8},
])
def test_default_rotates_batches_without_repeating_windows(settings, sample_video, budget, segmented):
    provider = LongVideoMock()
    if segmented:
        settings = replace(settings, long_video_threshold_seconds=1, long_sampling_fps=1,
                           long_window_seconds=8, long_window_stride_seconds=6)
    with TestClient(create_app(replace(settings, **budget), provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        started = client.post(f'/api/videos/{vid}/analyze').json()
        job = wait_job(client, started['id'])
        assert job['status'] == 'completed' and job['auto_continue']
        assert job['batch'] == 2 and job['calls'] == 2 and job['batch_calls'] == 1
        assert job['completed_windows'] == 2 and provider.windows == [0, 6000]
        assert len(client.get(f'/api/videos/{vid}/events').json()['events']) == 2


class BlockingMock(LongVideoMock):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def extract(self, **kwargs):
        self.entered.set()
        assert self.release.wait(10), 'test did not release the mock request'
        return super().extract(**kwargs)


def test_manual_pause_saves_inflight_result_and_resume_keeps_cache(settings, sample_video):
    provider = BlockingMock()
    with TestClient(create_app(replace(settings, max_calls=1), provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        started = client.post(f'/api/videos/{vid}/analyze').json()
        try:
            assert provider.entered.wait(10)
            pause = client.post(f'/api/videos/{vid}/pause')
            assert pause.status_code == 202 and pause.json()['pause_requested']
            pending = client.get(f"/api/jobs/{started['id']}").json()
            assert pending['status'] == 'running' and pending['pause_requested']
            assert client.post(f'/api/videos/{vid}/resume').status_code == 409
            assert client.delete(f'/api/videos/{vid}').status_code == 409
        finally:
            provider.release.set()
        paused = wait_job(client, started['id'])
        assert paused['status'] == 'paused' and paused['pause_reason'] == 'manual'
        assert paused['completed_windows'] == 1 and paused['calls'] == 1
        assert paused['failed_windows'] == [] and paused['error'] is None
        assert not paused['pause_requested'] and paused['resumable']
        cache = settings.data_dir/'runs'/started['id']/'window-00000.json'
        before = cache.read_bytes()
        assert client.post(f'/api/videos/{vid}/pause').json()['id'] == started['id']
        done = wait_job(client, client.post(f'/api/videos/{vid}/resume').json()['id'])
        assert done['status'] == 'completed' and done['calls'] == 2
        assert provider.windows == [0, 6000] and cache.read_bytes() == before
        assert client.post(f'/api/videos/{vid}/pause').status_code == 409


def configured_transport(settings, handler):
    configured = replace(settings, api_key='SYNTHETIC_TEST_KEY', enable_network=True,
                         billing_mode='request_limited', max_calls=1, max_daily_calls=1)
    return configured, QwenProvider(configured, Database(configured.data_dir), httpx.MockTransport(handler))


def test_daily_cap_survives_auto_batches_restart_and_resume(settings, sample_video):
    requests = []
    configured, provider = configured_transport(settings, lambda request: (
        requests.append(json.loads(request.content)) or httpx.Response(200, json=response())))
    with TestClient(create_app(configured, provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status'] == 'paused' and job['pause_reason'] == 'daily_limit'
        assert job['completed_windows'] == 1 and len(requests) == 1
        assert job['error']['code'] == 'REQUEST_LIMIT' and job['failed_windows'] == []
        assert client.app.state.db.usage_summary()['requests'] == 1
    with TestClient(create_app(configured, provider), base_url='http://localhost') as client:
        limited = client.post(f'/api/videos/{vid}/resume')
        assert limited.status_code == 429 and limited.json()['error']['code'] == 'REQUEST_LIMIT'
        # Simulate a new billing day in the isolated ledger, without changing system time.
        with client.app.state.db.connect() as connection:
            connection.execute("UPDATE requests SET day='2000-01-01'")
        resumed = wait_job(client, client.post(f'/api/videos/{vid}/resume').json()['id'])
        assert resumed['status'] == 'completed' and resumed['calls'] == 2 and len(requests) == 2
        assert client.app.state.db.usage_summary()['requests'] == 1


def test_provider_batch_request_ids_rotate_and_daily_ledger_accumulates(settings, sample_video):
    ids = []
    configured, provider = configured_transport(settings, lambda request: httpx.Response(200, json=response()))
    configured = replace(configured, max_daily_calls=4)
    provider = QwenProvider(configured, Database(configured.data_dir), provider.transport)
    with TestClient(create_app(configured, provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        with client.app.state.db.connect() as connection:
            ids = [row[0] for row in connection.execute('SELECT job_id FROM requests ORDER BY id')]
        assert job['status'] == 'completed' and ids == [job['id'], job['id']+'-b2']
        assert client.app.state.db.usage_summary()['requests'] == 2


@pytest.mark.parametrize('code,status', [('PROVIDER_UNAVAILABLE', 503), ('PROVIDER_TIMEOUT', 504),
                                        ('PROVIDER_RATE_LIMIT', 429), ('COST_LIMIT', 429)])
def test_service_or_cost_limits_do_not_start_another_batch(settings, sample_video, code, status):
    class FailingMock(LongVideoMock):
        def extract(self, **kwargs):
            self.windows.append(kwargs['window']['start_ms'])
            raise AppError(code, 'SYNTHETIC injected stop', status)

    provider = FailingMock()
    with TestClient(create_app(replace(settings, max_calls=1), provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status'] == 'paused' and job['batch'] == 1 and provider.windows == [0]
        assert job['error']['code'] == code


def test_retry_can_cross_batch_once_without_repeating_saved_work(settings, sample_video):
    class RetryMock(LongVideoMock):
        def __init__(self):
            super().__init__()
            self.failed = False

        def extract(self, **kwargs):
            if not self.failed:
                self.failed = True
                raise AppError('PROVIDER_TIMEOUT', 'SYNTHETIC transient timeout', 504, True)
            return super().extract(**kwargs)

    provider = RetryMock()
    with TestClient(create_app(replace(settings, max_calls=1), provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status'] == 'completed' and job['calls'] == 3 and job['batch'] == 3
        assert job['retries'] == 1 and provider.windows == [0, 6000]


def test_explicit_resume_call_limit_remains_a_hard_stop(settings, sample_video):
    provider = LongVideoMock()
    with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
        vid = upload(client, sample_video)
        # Hold the initial job before its first request, using the production pause signal.
        manager = client.app.state.manager
        original_run = manager.run
        def paused_run(video, job):
            manager.pause_requests[video['id']].set()
            original_run(video, job)
        manager.run = paused_run
        paused = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert paused['status'] == 'paused' and paused['calls'] == 0
        manager.run = original_run
        limited = wait_job(client, client.post(f'/api/videos/{vid}/resume?max_calls=1').json()['id'])
        assert limited['status'] == 'paused' and limited['calls'] == 1 and not limited['auto_continue']
        done = wait_job(client, client.post(f'/api/videos/{vid}/resume').json()['id'])
        assert done['status'] == 'completed' and done['auto_continue']

"""Offline upload-job integration: stub scanner, scripted providers, no model/GPU/API."""
from dataclasses import replace
import json
from pathlib import Path
import time

import httpx
import pytest

import backend.jobs as jobs
from backend.db import Database
from backend.errors import AppError
from backend.events import Extraction, validate_memory
from backend.hybrid_safety import HybridSafety as RealSafety, REVIEW_TOKENS
from backend.providers.base import ProviderResult
from backend.providers.qwen import QwenProvider
from backend.safety import RULES
from tests.test_provider import arguments, response


class OfflineProvider:
    # Simulate the real-provider accounting branches; ALL requests are local stubs.
    name, model, is_mock = 'TEST_OFFLINE', 'scripted-no-network', False

    def __init__(self, settings, db, trace):
        self.settings, self.db, self.trace = settings, db, trace

    def ready(self):
        pass

    def extract(self, *, window, video, job_id, **kwargs):
        self.trace.append(('objects', window['start_ms'], job_id))
        request = self.db.reserve_request(video['id'], job_id, self.settings.max_daily_calls, self.settings.max_calls)
        usage = {'prompt_tokens': 3, 'completion_tokens': 2}
        self.db.finish_request(request, 'success', {'kind': 'events', 'usage': usage})
        ids = [f['id'] for f in window['frames']]
        raw = {'entities': [{'id': 'item', 'label': 'SYNTHETIC object'},
                            {'id': 'person', 'label': 'SYNTHETIC person', 'entity_type': 'person'},
                            {'id': 'view', 'label': '摄像头画面', 'entity_type': 'location'}],
               'events': [{'subject_id': 'item', 'action': 'seen_at', 'frame_ids': ids[:1]},
                          {'subject_id': 'person', 'action': 'walk', 'frame_ids': ids},
                          {'subject_id': 'view', 'action': 'no_person_visible', 'frame_ids': ids}]}
        # Invalid references intentionally prove filtering happens BEFORE normalize.
        raw['events'] += [{'subject_id': 'undefined', 'action': action, 'frame_ids': ['forged']}
                          for action in RULES]
        return ProviderResult(Extraction.model_validate(raw), {'usage': usage})


class StubSafety:
    extraction = staticmethod(RealSafety.extraction)

    def __init__(self, settings, db, provider, specs, trace):
        self.settings, self.db, self.specs, self.trace = settings, db, specs, trace
        self.enabled = settings.hybrid_safety_enabled and not provider.is_mock
        self.reviews, self.scans = [], 0
        self.on_scan = self.on_review = None
        self.scan_error = None
        self.fail_review = {}

    def signature(self):
        config = Path(self.settings.safety_model_config)
        return ['offline-scan-v1', config.read_text() if config.is_file() else '', self.settings.review_model]

    def scan_video(self, video, run_dir, check_control, on_progress=None, on_candidate=None):
        self.scans += 1
        self.trace.append(('scan',))
        check_control()
        if self.on_scan:
            self.on_scan()
        check_control()
        if self.scan_error:
            raise self.scan_error
        # Scanner owns this directory; JobManager must never pre-create it.
        root = run_dir / 'safety'
        assert not root.exists()
        root.mkdir()
        candidates = []
        for index, spec in enumerate(self.specs):
            frames = []
            for part in range(spec.get('count', 2)):
                image = root / f'{index}-{part}.jpg'
                image.write_bytes(b'SYNTHETIC_STUB_BYTES_NOT_A_CAMERA_IMAGE')
                frames.append({'id': f'safety-{index}-{part}', 'pts_ms': 500+1000*part, 'path': str(image)})
            if spec.get('bad_kind'):
                frames[0]['kind'] = 'manual_placeholder'
            if 'bad_time' in spec:
                frames[0]['pts_ms'] = spec['bad_time']
            candidates.append({'id': 'sc_'+f'{index+1:032x}', 'event_type': spec.get('event_type', 'suspected_fall'),
                               'frames': frames, 'confidence': .99, 't': 1, 'occurred_at': '2026-10-31T00:00:00+00:00'})
        return candidates

    def review(self, candidate, video_id, job_id):
        index = int(candidate['id'][3:], 16)-1
        self.reviews.append(candidate['id'])
        self.trace.append(('review', candidate['id'], job_id))
        result = {'candidate_id': candidate['id'], 'event_type': candidate['event_type'],
                  'verdict': self.specs[index].get('verdict', 'confirmed'), 'reason': '离线测试视觉复核，非真实识别',
                  'frame_ids': [f['id'] for f in candidate['frames']], 'model': self.settings.review_model}
        if len(candidate['frames']) < 2:
            return {**result, 'verdict': 'uncertain', 'frame_ids': [], 'cloud_images': False}
        request = self.db.reserve_request(video_id, job_id, self.settings.max_daily_calls, self.settings.max_calls)
        if index in self.fail_review:
            error = self.fail_review[index]
            self.db.finish_request(request, 'failed', {'error': error.public()})
            raise error
        usage = {'usage': {'prompt_tokens': 3, 'completion_tokens': 2}}
        self.db.finish_request(request, 'success', {**usage, 'kind': 'verification'})
        if self.on_review:
            self.on_review()
        return {**result, 'usage': usage, 'cloud_images': True}


def finished(manager, job):
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        result = manager.job_snapshot(job['id'])
        if result['status'] not in {'queued', 'running'}:
            return result
        time.sleep(.05)
    raise AssertionError(f'offline job did not finish: {result}')


@pytest.fixture
def harness(settings, monkeypatch):
    managers = []

    def create(specs=(), **overrides):
        config = settings.data_dir.parent / 'stub-config.json'
        if not config.exists():
            config.write_text('{"offline":1}')
        options = dict(window_seconds=2, window_stride_seconds=2, max_daily_calls=100,
                       max_output_tokens=32768, activity_filter_enabled=False, safety_model_config=config)
        current = replace(settings, **(options | overrides))
        db, trace = Database(current.data_dir), []
        video = {'id': 'v_offline', 'sha256': 'a'*64, 'name': 'NOT_REAL_VIDEO.mp4', 'duration_ms': 4000,
                 'original_path': 'SYNTHETIC_NO_VIDEO', 'playback_path': 'SYNTHETIC_NO_VIDEO', 'metadata': {}}
        if not db.list_videos():
            db.add_video(video)
        provider = OfflineProvider(current, db, trace)
        safety = StubSafety(current, db, provider, specs, trace)
        monkeypatch.setattr(jobs, 'HybridSafety', lambda *args: safety)

        def sample(video, directory, profile):
            trace.append(('sample',))
            directory.mkdir(exist_ok=True)
            frames = []
            for index in range(4):
                path = directory / f'object-{index}.jpg'
                path.write_bytes(b'SYNTHETIC_OBJECT_FRAME')
                frames.append({'id': f'object-{index}', 'pts_ms': index*1000, 'kind': 'original_frame', 'path': str(path)})
            return frames

        monkeypatch.setattr(jobs, 'extract_frames', sample)
        manager = jobs.JobManager(current, db, provider)
        managers.append(manager)
        return manager, safety, provider, db, trace

    yield create
    for manager in managers:
        manager.close()


def test_scan_reviews_then_objects_filter_and_confirmed_memory(harness):
    specs = [{'verdict': 'confirmed'}, {'verdict': 'rejected'},
             {'event_type': 'help_requested', 'verdict': 'uncertain'},
             {'event_type': 'fire_detected'}, {'event_type': 'help_requested'}]
    manager, safety, provider, db, trace = harness(specs)
    job = finished(manager, manager.start('v_offline'))
    assert job['status'] == 'completed', job
    assert [item[0] for item in trace] == ['scan']+['review']*5+['sample', 'objects', 'objects']
    document, _ = db.raw_memory('v_offline')
    validate_memory(document)
    actions = [e['action'] for e in document['events']]
    assert {a for a in actions if a in RULES} == {'fall', 'help_gesture', 'flame'}
    assert all(actions.count(a) == 1 for a in ('fall', 'help_gesture', 'flame'))
    assert {'walk', 'seen_at', 'no_person_visible'} <= set(actions)
    assert len(db.safety_alerts('v_offline')) == 3
    assert all(f['kind'] == 'original_frame' and 0 <= f['pts_ms'] < 4000 for f in document['frames'])
    assert job['calls'] == db.usage_summary()['requests'] == 7
    assert job['frame_references'] == 14 and job['batch_output_budget'] == 5*REVIEW_TOKENS+2*4096
    assert job['usage'] == {'prompt_tokens': 21, 'completion_tokens': 14}
    assert len(job['safety']['candidates']) == len(job['safety']['reviews']) == 5
    assert {r['verdict'] for r in job['safety']['reviews'].values()} == {'confirmed', 'rejected', 'uncertain'}
    assert all('candidate_id' not in e and 'verdict' not in e and 'model' not in e for e in document['events'])
    cache = manager.settings.data_dir/'runs'/job['id']/'safety-reviews.json'
    assert cache.is_file() and json.loads(cache.read_text(encoding='utf8'))['safety']['scan_complete']


@pytest.mark.parametrize('limit,value,expected', [('max_calls', 1, 1), ('max_frame_references', 3, 1),
                                                 ('max_output_tokens', 4096, 4)])
def test_shared_batch_limits_resume_without_repaying(harness, limit, value, expected):
    manager, safety, provider, db, trace = harness([{} for _ in range(5)], **{limit: value})
    first = finished(manager, manager.start('v_offline', auto_continue=False))
    assert first['status'] == 'paused' and first['error']['code'] == 'BATCH_LIMIT', first
    assert first['resumable'] and len(safety.reviews) == expected
    assert not any(t[0] in {'objects', 'sample'} for t in trace)
    original, _ = db.raw_memory('v_offline')
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'completed' and done['calls'] == 7, done
    assert len(safety.reviews) == len(set(safety.reviews)) == 5 and safety.scans == 1
    after, _ = db.raw_memory('v_offline')
    assert {e['id'] for e in original['events']} <= {e['id'] for e in after['events']}
    assert db.usage_summary()['requests'] == 7
    assert len([t for t in trace if t[0] == 'objects']) == 2
    assert done['batch_output_budget'] <= manager.settings.max_output_tokens
    assert done['batch_frame_references'] <= manager.settings.max_frame_references


def test_response_sidecar_survives_failed_normalize_and_checkpoint_lag(harness, monkeypatch):
    manager, safety, provider, db, trace = harness([{}])
    original = jobs.normalize
    monkeypatch.setattr(jobs, 'normalize', lambda *args: (_ for _ in ()).throw(AppError('SYNTHETIC_NORMALIZE', 'test')))
    first = finished(manager, manager.start('v_offline'))
    assert first['status'] == 'paused' and first['calls'] == 1
    checkpoint = manager.settings.data_dir/'runs'/first['id']/'checkpoint.json'
    saved = json.loads(checkpoint.read_text(encoding='utf8'))
    assert saved['safety']['reviews'] and not saved['safety']['processed']
    # A crash can leave the checkpoint behind the independently committed response.
    saved['safety']['reviews'] = {}
    checkpoint.write_text(json.dumps(saved), encoding='utf8')
    lagging_job = db.job(first['id'])
    lagging_job.update(calls=0, frame_references=0, usage={'prompt_tokens': 0, 'completion_tokens': 0})
    db.save_job(lagging_job)
    monkeypatch.setattr(jobs, 'normalize', original)
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'completed' and done['calls'] == 3
    assert len(safety.reviews) == safety.scans == 1
    assert done['usage'] == {'prompt_tokens': 9, 'completion_tokens': 6}
    assert len([e for e in db.raw_memory('v_offline')[0]['events'] if e['action'] == 'fall']) == 1


@pytest.mark.parametrize('error', [AppError('PROVIDER_TIMEOUT', 'offline timeout', 504, True),
                                  AppError('SAFETY_REVIEW_INVALID', 'offline invalid response', 422)])
def test_failed_review_never_falls_back_or_automatically_repays(harness, error):
    manager, safety, provider, db, trace = harness([{}, {}, {'event_type': 'fire_detected'}])
    safety.fail_review[1] = error
    first = finished(manager, manager.start('v_offline'))
    assert first['status'] == 'paused' and first['calls'] == 2
    reviews = list(first['safety']['reviews'].values())
    assert [r['verdict'] for r in reviews] == ['confirmed', 'unavailable']
    assert [e['action'] for e in db.raw_memory('v_offline')[0]['events']] == ['fall']
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'partial' and done['calls'] == 5
    assert done['error']['code'] == error.code and '未复核' in done['stage']
    assert done['safety']['counts']['unavailable'] == 1
    assert len(safety.reviews) == len(set(safety.reviews)) == 3
    assert {e['action'] for e in db.raw_memory('v_offline')[0]['events'] if e['action'] in RULES} == {'fall', 'flame'}
    assert db.usage_summary()['failed'] == 1


def test_manual_pause_after_paid_response_keeps_it(harness):
    manager, safety, provider, db, trace = harness([{}, {}])
    safety.on_review = lambda: manager.pause_requests['v_offline'].set()
    first = finished(manager, manager.start('v_offline'))
    assert first['status'] == 'paused' and first['pause_reason'] == 'manual' and first['error'] is None
    assert first['calls'] == 1 and len(first['safety']['processed']) == 1
    safety.on_review = None
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'completed' and done['calls'] == 4
    assert len(safety.reviews) == len(set(safety.reviews)) == 2


def test_scan_pause_before_any_object_sampling_and_scan_failure_closed(harness):
    manager, safety, provider, db, trace = harness([{}])
    safety.on_scan = lambda: manager.pause_requests['v_offline'].set()
    first = finished(manager, manager.start('v_offline'))
    assert first['status'] == 'paused' and first['calls'] == 0 and first['resumable']
    assert trace == [('scan',)]
    safety.on_scan = None
    safety.scan_error = AppError('SAFETY_SCAN_FAILED', 'synthetic detector failure', 503, True)
    failed = finished(manager, manager.start('v_offline', resume=True))
    assert failed['status'] == 'paused' and failed['calls'] == 0
    assert not db.video('v_offline')['active_run']
    safety.scan_error = None
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'completed' and len(safety.reviews) == 1


def test_insufficient_frames_do_not_consume_cloud_budget(harness):
    manager, safety, provider, db, trace = harness([{'count': 1}])
    done = finished(manager, manager.start('v_offline'))
    assert done['status'] == 'completed' and done['calls'] == 2
    assert done['batch_output_budget'] == 8192 and done['frame_references'] == 4
    assert len(safety.reviews) == 1 and db.usage_summary()['requests'] == 2
    assert list(done['safety']['reviews'].values())[0]['verdict'] == 'uncertain'
    assert done['safety']['counts']['uncertain'] == 1 and done['error'] is None
    assert not db.safety_alerts('v_offline')


def test_daily_limit_shared_by_reviews_and_objects(harness):
    manager, safety, provider, db, trace = harness([{}, {}], max_daily_calls=1)
    first = finished(manager, manager.start('v_offline'))
    assert first['status'] == 'paused' and first['pause_reason'] == 'daily_limit'
    assert first['calls'] == db.usage_summary()['requests'] == len(safety.reviews) == 1
    assert not any(t[0] in {'sample', 'objects'} for t in trace)
    settings = replace(manager.settings, max_daily_calls=100)
    manager.settings = safety.settings = provider.settings = settings
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'completed' and done['calls'] == 4
    assert len(safety.reviews) == len(set(safety.reviews)) == 2


@pytest.mark.parametrize('field,value', [('bad_time', -1), ('bad_time', 4000), ('bad_kind', True)])
def test_candidate_original_frame_kind_and_time_checked_before_payment(harness, field, value):
    manager, safety, provider, db, trace = harness([{field: value}])
    job = finished(manager, manager.start('v_offline'))
    assert job['status'] == 'paused' and job['calls'] == 0
    assert job['error']['code'] in {'PLACEHOLDER_EVIDENCE', 'FRAME_TIME', 'MEMORY_SCHEMA'}
    assert not safety.reviews and not db.video('v_offline')['active_run']


@pytest.mark.parametrize('change', ['old_pipeline', 'config_content', 'enabled', 'review_model', 'sidecar'])
def test_resume_rejects_stale_pipeline_or_config(harness, change):
    manager, safety, provider, db, trace = harness([{}, {}], max_calls=1)
    first = finished(manager, manager.start('v_offline', auto_continue=False))
    root = manager.settings.data_dir/'runs'/first['id']
    if change == 'old_pipeline':
        path = root/'checkpoint.json'
        saved = json.loads(path.read_text(encoding='utf8'))
        saved['signature'] = saved['signature'][:-1]
        path.write_text(json.dumps(saved), encoding='utf8')
    elif change == 'config_content':
        manager.settings.safety_model_config.write_text('{"offline":2}')
    elif change == 'enabled':
        safety.enabled = False
    elif change == 'review_model':
        safety.settings = replace(safety.settings, review_model='different-offline-model')
    else:
        path = root/'safety-reviews.json'
        saved = json.loads(path.read_text(encoding='utf8'))
        saved['signature'] = ['stale']
        path.write_text(json.dumps(saved), encoding='utf8')
        done = finished(manager, manager.start('v_offline', resume=True))
        assert done['status'] == 'failed' and done['error']['code'] == 'CACHE_MISMATCH'
        assert len(safety.reviews) == 1
        return
    with pytest.raises(AppError) as caught:
        manager.start('v_offline', resume=True)
    assert caught.value.code == 'CHECKPOINT_CONFIG'
    assert len(safety.reviews) == 1


def test_real_hybrid_initialization_mock_provider_never_scans(settings, monkeypatch):
    class Mock:
        name, model, is_mock = 'TEST_MOCK', 'no-model', True
    monkeypatch.setattr(RealSafety, 'scan_video', lambda *a, **k: pytest.fail('mock must not load detector'))
    monkeypatch.setattr(RealSafety, 'signature', lambda *a: pytest.fail('mock must not hash/load weights'))
    manager = jobs.JobManager(settings, Database(settings.data_dir), Mock())
    try:
        assert not manager.safety.enabled
        assert manager.signature({'duration_ms': 4000})[-1] == [jobs.PIPELINE_VERSION, False, None]
    finally:
        manager.close()


@pytest.mark.parametrize('enabled,is_mock,explicit,expected', [
    (True, False, None, True), (False, False, None, False), (True, True, None, False),
    (True, False, False, False), (False, False, True, True)])
def test_qwen_object_only_is_optional_and_uses_only_mock_transport(settings, tmp_path, enabled, is_mock, explicit, expected):
    captured = []

    def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    current = replace(settings, hybrid_safety_enabled=enabled, api_key='OFFLINE-ONLY', enable_network=True,
                      billing_mode='request_limited')
    provider = QwenProvider(current, Database(current.data_dir), httpx.MockTransport(transport))
    provider.is_mock = is_mock
    args = arguments(tmp_path)
    if explicit is not None:
        args['object_only'] = explicit
    result = provider.extract(**args)
    text = captured[0]['messages'][0]['content'][0]['text']
    assert ('【物品/寻迹模式 object_only】' in text) == expected
    assert (result.usage.get('extraction_mode') == 'object_only') == expected
    assert result.usage['prompt_sha256'] == provider.event_prompt.sha256
    assert len(captured) == 1

def test_restart_preserves_reviews_frames_and_request_accounting(harness):
    specs = [{}, {'event_type': 'help_requested'}, {'event_type': 'fire_detected'}]
    manager, safety, provider, db, trace = harness(specs, max_calls=2)
    first = finished(manager, manager.start('v_offline', auto_continue=False))
    assert first['status'] == 'paused' and len(safety.reviews) == 2
    original, _ = db.raw_memory('v_offline')
    manager.close()
    restarted, new_safety, _, db, new_trace = harness(specs, max_calls=2)
    done = finished(restarted, restarted.start('v_offline', resume=True))
    assert done['status'] == 'completed' and done['calls'] == 5
    assert new_safety.scans == 0 and len(new_safety.reviews) == 1
    assert not any(t[0] == 'scan' for t in new_trace)
    after, _ = db.raw_memory('v_offline')
    assert {e['id'] for e in original['events']} <= {e['id'] for e in after['events']}
    assert len([e for e in after['events'] if e['action'] in RULES]) == 3
    assert db.usage_summary()['requests'] == 5


def test_interrupted_paid_outcome_is_unavailable_gap_not_reissued(harness):
    manager, safety, provider, db, trace = harness([{}])

    def crash_after_dispatch():
        raise RuntimeError('synthetic crash before response cache')

    safety.on_review = crash_after_dispatch
    first = finished(manager, manager.start('v_offline'))
    assert first['status'] == 'failed' and first['calls'] == 1
    safety.on_review = None
    done = finished(manager, manager.start('v_offline', resume=True))
    assert done['status'] == 'partial' and done['calls'] == 3
    assert len(safety.reviews) == 1 and done['safety']['counts']['unavailable'] == 1
    assert done['error']['code'] == 'SAFETY_REVIEW_UNAVAILABLE'
    assert not db.safety_alerts('v_offline')

def test_safety_mode_skips_objects_and_empty_scan_is_success(harness):
    manager, safety, provider, db, trace = harness()
    job = finished(manager, manager.start('v_offline', mode='safety'))
    assert job['status'] == 'completed', job
    assert job['analysis_mode'] == 'safety'
    assert job['progress'] == 100 and job['total_windows'] == 0
    assert job['calls'] == 0 and trace == [('scan',)]
    document, _ = db.raw_memory('v_offline')
    assert document['events'] == []
    plan = manager.plan('v_offline', 'safety')
    assert plan['estimated_requests_without_retries'] is None
    assert plan['total_windows'] == 0


def test_objects_mode_never_loads_safety_or_emits_alarms(harness):
    manager, safety, provider, db, trace = harness([{}])
    safety.scan_error = AppError('MUST_NOT_SCAN', 'not used')
    job = finished(manager, manager.start('v_offline', mode='objects'))
    assert job['status'] == 'completed', job
    assert job['analysis_mode'] == 'objects'
    assert safety.scans == 0 and not safety.reviews
    assert [entry[0] for entry in trace] == ['sample', 'objects', 'objects']
    document, _ = db.raw_memory('v_offline')
    assert not any(event['action'] in RULES for event in document['events'])
    assert manager.plan('v_offline', 'objects')['hybrid_safety_enabled'] is False


def test_independent_modes_retain_other_results_and_playable_evidence(harness):
    manager, safety, provider, db, trace = harness([{}])
    first = finished(manager, manager.start('v_offline', mode='safety'))
    assert first['status'] == 'completed', first
    original, _ = db.raw_memory('v_offline')
    fall = next(e for e in original['events'] if e['action'] == 'fall')
    second = finished(manager, manager.start('v_offline', mode='objects'))
    assert second['status'] == 'completed', second
    combined, _ = db.raw_memory('v_offline')
    assert {'fall', 'seen_at'} <= {e['action'] for e in combined['events']}
    kept_fall = next(e for e in combined['events'] if e['action'] == 'fall')
    assert {k:v for k,v in kept_fall.items() if k != 'id'} == {k:v for k,v in fall.items() if k != 'id'}
    assert second['retained_analysis']['event_id_mapping'][fall['id']] == kept_fall['id']
    assert second['retained_analysis']['run_id'] == first['id']
    for frame in combined['frames']:
        assert (manager.settings.data_dir/'runs'/second['id']/'frames'/f"{frame['id']}.jpg").is_file()
    third = finished(manager, manager.start('v_offline', mode='safety'))
    assert third['status'] == 'completed', third
    final, _ = db.raw_memory('v_offline')
    assert {'fall', 'seen_at'} <= {e['action'] for e in final['events']}
    assert sum(e['action'] == 'fall' for e in final['events']) == 1


def test_invalid_mode_rejected_before_scan_or_cloud(harness):
    manager, safety, provider, db, trace = harness()
    with pytest.raises(AppError) as err:
        manager.start('v_offline', mode='unknown')
    assert err.value.code == 'ANALYSIS_MODE'
    assert not trace


def test_streamed_candidate_review_is_visible_before_eof(harness):
    manager, safety, provider, db, trace = harness([{}])
    scan = safety.scan_video
    observed = []
    def streaming(video, run_dir, check_control, on_progress, on_candidate):
        candidates = scan(video, run_dir, check_control)
        on_progress({'frames': 60, 'total_frames': 120, 'progress': 50})
        on_candidate(candidates[0])
        job = db.latest_job(video['id'])
        observed.append((job['safety']['scan_complete'], job['safety']['scan_progress'],
                         job['safety']['confirmed'], len(db.raw_memory(video['id'])[0]['events'])))
        trace.append(('eof',))
        return candidates
    safety.scan_video = streaming
    job = finished(manager, manager.start('v_offline', mode='safety'))
    assert job['status'] == 'completed', job
    assert observed == [(False, 50, 1, 1)]
    assert [item[0] for item in trace] == ['scan', 'review', 'eof']
    assert job['calls'] == 1

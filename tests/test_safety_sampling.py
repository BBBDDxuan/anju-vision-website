"""Synthetic sampling/episode regressions; no paid model calls."""
import copy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.answer import query_memory
from backend.main import create_app
from backend.media import extract_frames, inspect_video
from backend.safety import derive_alerts
from tests.conftest import MockVisionProvider
from tests.test_environment_hazards import document as environment_document
from tests.test_safety import document as person_document
from tests.test_long_video import LongVideoMock
from tests.conftest import wait_job


def test_enhanced_sampling_plan_keeps_short_and_long_paths_separate(settings, sample_video):
    enhanced = replace(settings, enhanced_safety_sampling=True)
    with TestClient(create_app(enhanced, MockVisionProvider()), base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        plan = client.get(f'/api/videos/{vid}/analysis-plan').json()
        assert (plan['sampling_fps'], plan['window_seconds'], plan['total_windows']) == (4, 4, 4)
        assert plan['profile'] == 'short' and plan['sampling_mode'] == 'full_clip'
        assert plan['enhanced_safety_sampling'] and not plan['activity_filter_enabled']
        manager = client.app.state.manager
        short = manager.profile({'duration_ms': 12000})
        long = manager.profile({'duration_ms': 121000})
        assert short.window_stride_seconds == 3
        assert (long.sampling_fps, long.window_seconds, long.window_stride_seconds) == (.5, 30, 28)
        assert manager.filter_enabled({'duration_ms': 121000})
        assert plan['max_windows_per_batch'] * 16 <= settings.max_frame_references


def test_subsecond_sampling_has_real_source_timestamps(settings, sample_video, tmp_path):
    video = inspect_video(sample_video, settings)
    video['original_path'] = str(sample_video)
    dense = extract_frames(video, tmp_path/'dense', replace(settings, sampling_fps=4))
    sparse = extract_frames(video, tmp_path/'sparse', settings)
    # A synthetic 1.2–1.9s phenomenon is missed by integer-second sampling.
    assert not any(1200 <= f['pts_ms'] <= 1900 for f in sparse)
    assert len([f for f in dense if 1200 <= f['pts_ms'] <= 1900]) >= 2
    assert all(f['kind'] == 'original_frame' and f['id'].startswith('f') for f in dense)


def test_enhanced_short_video_runs_end_to_end_with_explicit_mock(settings, sample_video):
    provider = LongVideoMock()
    enhanced = replace(settings, enhanced_safety_sampling=True, max_output_tokens=32768, max_frame_references=256)
    with TestClient(create_app(enhanced, provider), base_url='http://localhost') as client:
        vid = client.post('/api/videos/upload', content=sample_video.read_bytes()).json()['video_id']
        job = wait_job(client, client.post(f'/api/videos/{vid}/analyze').json()['id'])
        assert job['status'] == 'completed' and job['completed_windows'] == 4
        assert provider.windows == [0, 3000, 6000, 9000]
        memory = client.get(f'/api/videos/{vid}/events').json()
        assert memory['is_mock'] and len(memory['frames']) == 48
        assert client.app.state.db.usage_summary()['requests'] == 0


@pytest.mark.parametrize('action,expected', [('flame', 2), ('sparks', 2), ('smoke', 1)])
def test_extinguished_bursts_do_not_merge_in_alerts(action, expected):
    memory = environment_document(action)
    second = copy.deepcopy(memory['events'][0])
    second.update(id='second', start_ms=1500, end_ms=2500, evidence_ids=['second-evidence'])
    memory['frames'] += [{'id': 'f2', 'pts_ms': 1500, 'kind': 'original_frame'},
                         {'id': 'f3', 'pts_ms': 2500, 'kind': 'original_frame'}]
    memory['evidence'].append({**memory['evidence'][0], 'id': 'second-evidence', 'frame_ids': ['f2', 'f3']})
    memory['events'].append(second)
    memory['video_duration_ms'] = 3000
    alerts = derive_alerts(memory, 'j_SYNTHETIC_BURSTS')
    assert len(alerts) == expected
    assert {eid for a in alerts for eid in a['event_ids']} == {e['id'] for e in memory['events']}


@pytest.mark.parametrize('action,question', [('fall', '视频中什么时候有人跌倒？'),
    ('help_gesture', '画面里有人求助吗？'), ('walk', '这段视频有人行走吗？')])
def test_generic_person_questions_return_evidence(action, question):
    result = query_memory(person_document(action), {'status': 'completed'}, question)
    assert result['observations'] and result['evidence'] and not result['candidates']


def test_explicit_missing_person_does_not_fall_back_to_everyone():
    memory = person_document()
    for question, entity in [('视频中人物99什么时候跌倒？', None), ('视频中有人跌倒吗？', 'missing')]:
        result = query_memory(memory, {'status': 'completed'}, question, entity_id=entity)
        assert not result['observations'] and not result['evidence']

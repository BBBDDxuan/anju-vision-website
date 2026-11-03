"""24h boundary/planning uses mocked metadata; media integration uses only real short synthetic files."""
from dataclasses import replace
from urllib.parse import quote
from unittest.mock import patch
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from backend.answer import clock
from backend.errors import AppError
from backend.main import create_app
from backend.media import extract_segment_frames, inspect_video
from tests.conftest import wait_job
from tests.test_long_video import LongVideoMock


def test_24h_duration_boundary_is_metadata_only(settings, sample_video):
    info = {'streams': [{'codec_type': 'video', 'duration': '86400', 'width': 320, 'height': 180}], 'format': {}}
    with patch('backend.media.command_json', return_value=info):
        assert inspect_video(sample_video, settings)['duration_ms'] == 86400000
        info['streams'][0]['duration'] = '86401'
        with pytest.raises(AppError) as error:
            inspect_video(sample_video, settings)
        assert error.value.code == 'VIDEO_DURATION'


def test_day_plan_does_not_decode_or_call_model(settings, sample_video):
    with TestClient(create_app(settings, LongVideoMock()), base_url='http://localhost') as client:
        # A planning-only synthetic metadata record, never presented as an analyzed 24h file.
        client.app.state.db.add_video({'id': 'v_TEST_METADATA_ONLY_DAY', 'sha256': '0'*64,
            'name': 'MOCK_DURATION_ONLY_NOT_A_REAL_DAY.mp4', 'duration_ms': 86400000,
            'original_path': str(sample_video), 'playback_path': str(sample_video), 'metadata': {'synthetic_duration_only': True}})
        with patch('backend.jobs.extract_frames', side_effect=AssertionError('must not decode')):
            plan = client.get('/api/videos/v_TEST_METADATA_ONLY_DAY/analysis-plan').json()
        assert plan['total_windows'] == 3086 and plan['sampling_mode'] == 'on_demand_segments'
        assert plan['estimated_sampled_frames'] == 43200
        assert client.app.state.db.usage_summary()['requests'] == 0


def test_segmented_batch_resume_only_samples_the_needed_short_window(settings, sample_video):
    settings = replace(settings, long_video_threshold_seconds=1, long_sampling_fps=1,
                       long_window_seconds=8, long_window_stride_seconds=6, max_calls=1,
                       activity_filter_enabled=False)  # Isolate segment sampling; activity-gate probes have separate tests.
    provider = LongVideoMock()
    with patch('backend.jobs.extract_frames', side_effect=AssertionError('full decode forbidden')), \
         patch('backend.jobs.extract_segment_frames', wraps=extract_segment_frames) as sample:
        with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
            vid = client.post('/api/videos/upload', content=sample_video.read_bytes(), headers={'x-video-name': 'SHORT_SYNTHETIC.mp4'}).json()['video_id']
            job = wait_job(client, client.post(f'/api/videos/{vid}/analyze?auto_continue=false').json()['id'])
            assert job['status'] == 'paused' and len(sample.call_args_list) == 1
            assert job['analyzed_ranges'] == [{'start_ms': 0, 'end_ms': 8000}]
            document, _ = client.app.state.db.raw_memory(vid)
            assert max(f['pts_ms'] for f in document['frames']) == 7000
            job_id = job['id']
        with TestClient(create_app(settings, provider), base_url='http://localhost') as client:
            resumed = wait_job(client, client.post(f'/api/videos/{vid}/resume').json()['id'])
            assert resumed['id'] == job_id and resumed['status'] == 'completed'
            assert provider.windows == [0, 6000] and len(sample.call_args_list) == 2
            assert sample.call_args_list[-1].args[3:5] == (6000, 12000)
            memory = client.get(f'/api/videos/{vid}/events').json()
            assert memory['events'][-1]['end_ms'] == 11000
            frame = memory['frames'][-1]
            assert client.get(frame['url']).status_code == 200


def test_stream_upload_filename_and_byte_limit(settings, sample_video):
    with TestClient(create_app(settings, LongVideoMock()), base_url='http://localhost') as client:
        data = sample_video.read_bytes()
        result = client.post('/api/videos/upload', content=data, headers={'x-video-name': quote('居家录像.mp4')})
        assert result.status_code == 201, result.text
        vid = result.json()['video_id']
        assert result.json()['name'] == '居家录像.mp4'
        assert client.get(f'/api/videos/{vid}').json()['metadata']['file_size_bytes'] == len(data)
    with TestClient(create_app(replace(settings, data_dir=settings.data_dir/'limited', max_upload_bytes=8), LongVideoMock()), base_url='http://localhost') as client:
        assert client.post('/api/videos/upload', content=data).status_code == 413
        assert client.get('/api/videos').json()['videos'] == []


def test_stream_upload_rejects_insufficient_space_and_cleans_partial(settings, sample_video):
    with TestClient(create_app(settings, LongVideoMock()), base_url='http://localhost') as client:
        disk = shutil._ntuple_diskusage(1000, 999, 1)
        with patch('backend.main.shutil.disk_usage', return_value=disk):
            result = client.post('/api/videos/upload', content=sample_video.read_bytes())
        assert result.status_code == 413 and result.json()['error']['code'] == 'DISK_SPACE'
        assert not list((settings.data_dir/'videos').iterdir())


def test_bounded_seek_uses_real_pts_with_nonzero_origin(settings, sample_video, tmp_path):
    target = tmp_path/'offset-pts.mp4'
    result = subprocess.run([settings.ffmpeg, '-v', 'error', '-y', '-i', str(sample_video), '-an', '-vf',
                             'setpts=PTS+5/TB', '-vsync', '0', '-vcodec', 'libx264', '-bf', '0', str(target)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    meta = inspect_video(target, settings)
    assert meta['start_seconds'] == 5
    frames = extract_segment_frames({'original_path': str(target), 'duration_ms': meta['duration_ms'], 'metadata': meta},
                                     tmp_path/'offset-frames', settings, 6000, 10000, 1)
    assert [f['pts_ms'] for f in frames] == [6000, 7000, 8000, 9000]
    assert clock(frames[-1]['pts_ms']) == '00:09.0' and clock(86399000) == '23:59:59.0'

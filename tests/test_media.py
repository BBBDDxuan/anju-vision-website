from dataclasses import replace
import subprocess

import pytest

from backend.errors import AppError
from backend.media import extract_frames, inspect_video, windows, prepare_playback


def test_original_pts_sampling(settings, sample_video, tmp_path):
    meta = inspect_video(sample_video, settings)
    frames = extract_frames({"original_path": str(sample_video), **meta}, tmp_path / "frames", settings)
    assert [f["pts_ms"] for f in frames] == list(range(0, 12000, 1000))
    chunks = list(windows(frames, meta["duration_ms"]))
    assert len(chunks) == 2 and sum(len(w["frames"]) for w in chunks) == 14


def test_duration_limit(settings, sample_video):
    with pytest.raises(AppError) as error:
        inspect_video(sample_video, replace(settings, max_video_seconds=1))
    assert error.value.code == "VIDEO_DURATION"


def test_vfr_pts_are_not_replaced_by_frame_number(settings, sample_video, tmp_path):
    target = tmp_path / "variable-timing.mp4"
    p = subprocess.run([settings.ffmpeg, "-v", "error", "-y", "-i", str(sample_video), "-an", "-vf", "setpts=if(lt(N\\,60)\\,N/(10*TB)\\,(6+(N-60)/5)/TB)", "-vsync", "0", "-vcodec", "libx264", "-bf", "0", str(target)], capture_output=True, timeout=30)
    assert p.returncode == 0, p.stderr
    meta = inspect_video(target, settings)
    frames = extract_frames({"original_path": str(target), **meta}, tmp_path / "vfr-frames", settings)
    assert frames[-1]["pts_ms"] >= 16000
    assert frames[-1]["source_frame_index"] < 120
    playback = tmp_path / "vfr-playback.mp4"
    prepare_playback(target, playback, settings)
    playback_meta = inspect_video(playback, settings)
    played_frames = extract_frames({"original_path": str(playback), **playback_meta}, tmp_path / "played-frames", settings)
    assert len(played_frames) == len(frames)
    assert all(abs(a["pts_ms"] - b["pts_ms"]) <= 1 for a, b in zip(frames, played_frames))

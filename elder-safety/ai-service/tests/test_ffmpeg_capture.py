"""Uncompressed decoder framing, source fidelity, and cancellation; no camera."""
import io
import struct
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ai_service.ffmpeg_capture import FFmpegCaptureLink, _BmpStream, read_bgr_bmp


@pytest.mark.parametrize("width", [5, 6, 7, 8, 2880])
def test_bmp_preserves_original_pixels_orientation_and_row_padding(width):
    image = np.arange(3 * width * 3, dtype=np.uint8).reshape(3, width, 3)
    ok, data = cv2.imencode(".bmp", image)
    assert ok
    result = read_bgr_bmp(io.BytesIO(data.tobytes()))
    np.testing.assert_array_equal(result, image)
    assert result.flags.c_contiguous


def test_top_down_bmp_is_not_flipped():
    image = np.arange(3 * 8 * 3, dtype=np.uint8).reshape(3, 8, 3)
    ok, data = cv2.imencode(".bmp", image[::-1])
    assert ok
    data = bytearray(data)
    struct.pack_into("<i", data, 22, -3)
    np.testing.assert_array_equal(read_bgr_bmp(io.BytesIO(data)), image)


@pytest.mark.parametrize("field,value", [(2, 0xffffffff), (10, 1), (18, 0), (30, 1)])
def test_decoder_rejects_invalid_or_unbounded_frames(field, value):
    ok, data = cv2.imencode(".bmp", np.zeros((3, 5, 3), np.uint8))
    assert ok
    data = bytearray(data)
    struct.pack_into("<I", data, field, value)
    with pytest.raises(ValueError, match="invalid_decoder_frame"):
        read_bgr_bmp(io.BytesIO(data))


def test_truncated_decoder_output_fails_promptly():
    with pytest.raises(EOFError):
        read_bgr_bmp(io.BytesIO(b"BM"))


def test_waiting_for_first_frame_is_cancellable():
    link = object.__new__(FFmpegCaptureLink)
    link.config = SimpleNamespace(open_timeout_seconds=8)
    link.reader = SimpleNamespace(condition=threading.Condition(), failed=False, latest=None)
    stop = threading.Event()
    stop.set()
    assert not link.open(stop)


def test_decoder_cleanup_kills_an_unresponsive_owned_process():
    import subprocess

    class Process:
        terminated = killed = False
        def poll(self): return None
        def terminate(self): self.terminated = True
        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("decoder", timeout)
            return 0
        def kill(self): self.killed = True

    process = Process()
    stdout = io.BytesIO()
    stream = _BmpStream(process, stdout)
    stream.release()
    stream.release()
    assert process.terminated and process.killed and stdout.closed


def test_decoder_first_frame_wait_has_a_hard_deadline(monkeypatch):
    import ai_service.ffmpeg_capture as capture
    clock = [0.0]

    class Condition:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def wait(self, timeout): clock[0] += timeout

    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    link = object.__new__(FFmpegCaptureLink)
    link.config = SimpleNamespace(open_timeout_seconds=8)
    link.reader = SimpleNamespace(condition=Condition(), failed=False, latest=None)
    assert not link.open(threading.Event())
    assert clock[0] == 10

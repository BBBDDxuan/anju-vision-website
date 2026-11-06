"""Owned FFmpeg decoder with original-resolution, uncompressed BGR frames.

The web worker already has NumPy loaded. Decoding in FFmpeg avoids starting
another Python interpreter and loading NumPy/OpenCV again on every connection.
"""
from __future__ import annotations

from pathlib import Path
import os
import struct
import subprocess
import threading
import time

import numpy as np

from .sources import _LatestCapture

MAX_FRAME_BYTES = 128_000_000


def read_exact(stream, size):
    parts = bytearray(size)
    view = memoryview(parts)
    offset = 0
    while offset < size:
        count = stream.readinto(view[offset:])
        if not count:
            raise EOFError
        offset += count
    return parts


def read_bgr_bmp(stream):
    header = read_exact(stream, 54)
    size, offset = struct.unpack_from("<I4xI", header, 2)
    dib, width, signed_height, planes, bits, compression = struct.unpack_from("<IiiHHI", header, 14)
    height = abs(signed_height)
    stride = ((width * 3 + 3) // 4) * 4
    if (header[:2] != b"BM" or dib < 40 or planes != 1 or bits != 24 or compression != 0
            or width <= 0 or height <= 0 or not 54 <= offset <= size <= MAX_FRAME_BYTES
            or offset < 14 + dib or offset + stride * height != size):
        raise ValueError("invalid_decoder_frame")
    data = read_exact(stream, size - 54)
    image = np.frombuffer(data, np.uint8, count=stride * height, offset=offset - 54)
    image = image.reshape(height, stride)[:, :width * 3].reshape(height, width, 3)
    return (image[::-1] if signed_height > 0 else image).copy()


class _BmpStream:
    def __init__(self, process, stdout):
        self.process = process
        self.stdout = stdout
        self.lock = threading.Lock()
        self.closed = False

    def read(self):
        try:
            return True, read_bgr_bmp(self.stdout)
        except (EOFError, OSError, ValueError):
            return False, None

    def release(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            self.stdout.close()


class FFmpegCaptureLink:
    """Same capture contract and latest-frame semantics, without Python spawn."""

    def __init__(self, url, config):
        ffmpeg = Path(config.recording.ffmpeg_path or "")
        if not ffmpeg.is_absolute() or not ffmpeg.is_file():
            raise ValueError("capture_tool_missing")
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin"]
        if url.lower().startswith(("rtsp://", "rtsps://")):
            command += ["-rtsp_transport", "tcp", "-timeout", str(int(config.open_timeout_seconds * 1_000_000))]
        command += ["-i", url, "-map", "0:v:0", "-an", "-sn", "-dn",
                    "-fps_mode", "passthrough", "-c:v", "bmp", "-pix_fmt", "bgr24",
                    "-f", "image2pipe", "pipe:1"]
        self.config = config
        # As with the existing recorder, native errors (which can contain the
        # source credentials) never enter logs or the web worker's protocol.
        stdout, writer = None, None
        if os.name == "nt":
            import _winapi
            import msvcrt
            # Windows' default 4 KiB anonymous pipe serializes every tiny
            # fragment of a multi-megabyte frame. Reserve a bounded 1 MiB pipe.
            read_handle, write_handle = _winapi.CreatePipe(None, 1_048_576)
            stdout = os.fdopen(msvcrt.open_osfhandle(read_handle, os.O_RDONLY | os.O_BINARY), "rb", buffering=0)
            writer = os.fdopen(msvcrt.open_osfhandle(write_handle, os.O_WRONLY | os.O_BINARY), "wb", buffering=0)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=writer if writer is not None else subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except BaseException:
            if stdout is not None:
                stdout.close()
            raise
        finally:
            if writer is not None:
                writer.close()
        self.stream = _BmpStream(self.process, stdout if stdout is not None else self.process.stdout)
        self.reader = _LatestCapture(self.stream)

    def open(self, stop):
        deadline = time.monotonic() + self.config.open_timeout_seconds + 2
        with self.reader.condition:
            while not stop.is_set() and not self.reader.failed:
                if self.reader.latest is not None:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.reader.condition.wait(min(.1, remaining))
        return False

    def read(self, stop):
        if stop.is_set():
            return "failed", None
        return self.reader.read(self.config.read_timeout_seconds)

    def close(self):
        self.reader.close()
        self.stream.release()
        self.reader.thread.join(timeout=1)

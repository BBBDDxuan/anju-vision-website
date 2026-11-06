"""Lightweight spawn entrypoint; runtime loading has its own bounded deadline."""
from __future__ import annotations

import os


def capture_process(pipe, url: str, open_ms: int, read_ms: int):
    # This module must not import NumPy/OpenCV at module scope: on Windows those
    # imports run during spawn, before the parent can distinguish initialization
    # from a stalled camera connection. Never forward native camera diagnostics.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    try:
        from .sources import _capture_child
        pipe.send(("ready", None))
    except (Exception, KeyboardInterrupt):
        try:
            pipe.send(("failed", None))
        except (OSError, EOFError):
            pass
        pipe.close()
        return
    _capture_child(pipe, url, open_ms, read_ms)

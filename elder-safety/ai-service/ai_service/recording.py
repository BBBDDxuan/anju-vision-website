"""Private local RTSP stream-copy recording and bounded, ownership-scoped retention.

Never records through the inference loop; never logs URLs/native diagnostics.
Cleanup is flat, nonrecursive and restricted to explicitly registered sessions.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from .config import RecordingConfig

GIB = 1024**3
SCHEMA = "elder-safety-camera-recordings-v1"
PREFIX = re.compile(r"camera_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{32}")
VIDEO = re.compile(r"(camera_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{32})_([0-9]{6,})[.]mkv")


def plain_path(path):
    """Reject symlinks and Windows junctions, including any existing ancestor."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            return False
    return True


class RecordingStore:
    def __init__(self, directory, *, disk_usage=shutil.disk_usage):
        self.root = Path(directory)
        if not self.root.is_absolute() or not plain_path(self.root):
            raise ValueError("unsafe_recording_directory")
        # A dedicated folder, never a drive root or a broad top-level directory.
        # Ownership markers and the non-empty-directory check below provide the
        # deletion boundary; the drive letter itself must remain portable.
        if len(self.root.parts) < 3:
            raise ValueError("recording_requires_dedicated_folder")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve(strict=True)
        marker = self.root/".elder-safety-recordings.json"
        if marker.exists():
            if not plain_path(marker) or marker.stat().st_size > 4096 or json.loads(marker.read_text(encoding="utf-8")) != {"schema": SCHEMA}:
                raise ValueError("invalid_recording_owner")
        else:
            if any(self.root.iterdir()):
                raise ValueError("nonempty_unowned_recording_directory")
            with marker.open("x", encoding="utf-8") as output:
                json.dump({"schema": SCHEMA}, output)
        self.disk_usage = disk_usage
        self.deleted = 0
        self.lock_file = None
        lock = self.root/".recording.lock"
        if not plain_path(lock):
            raise ValueError("unsafe_recording_lock")
        handle = lock.open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file = handle
        except Exception:
            handle.close()
            raise ValueError("recording_directory_busy") from None

    def guard(self):
        marker = self.root/".elder-safety-recordings.json"
        if (not plain_path(self.root) or not plain_path(marker) or not marker.is_file()
                or marker.stat().st_size > 4096
                or json.loads(marker.read_text(encoding="utf-8")) != {"schema": SCHEMA}):
            raise ValueError("recording_owner_changed")

    def new_session(self):
        self.guard()
        prefix = "camera_"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex
        with (self.root/("."+prefix+".json")).open("x", encoding="utf-8") as output:
            json.dump({"schema": SCHEMA, "prefix": prefix}, output)
        return prefix

    def files(self):
        self.guard()
        owners = set()
        children = list(self.root.iterdir())
        for path in children:
            prefix = path.name[1:-5] if path.name.startswith(".") and path.name.endswith(".json") else ""
            if not PREFIX.fullmatch(prefix) or not plain_path(path) or not path.is_file() or path.stat().st_size > 4096:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data == {"schema": SCHEMA, "prefix": prefix}:
                    owners.add(prefix)
            except (ValueError, OSError):
                continue
        result = []
        for path in children:
            match = VIDEO.fullmatch(path.name)
            if match and match[1] in owners and plain_path(path) and path.is_file() and path.resolve().parent == self.root:
                info = path.stat()
                result.append((path, info.st_mtime, info.st_size))
        return sorted(result, key=lambda row: (row[1], row[0].name))

    def cleanup(self, config, active=None, *, now=None):
        """Delete oldest completed owned files only; never unlink active output."""
        now = time.time() if now is None else now
        files = self.files()
        total = sum(row[2] for row in files)
        active = Path(active) if active is not None else None
        free = self.disk_usage(self.root).free
        for path, modified, size in files:
            expired = now-modified > config.retention_days*86400
            if not expired and total <= config.max_gib*GIB and free >= config.min_free_gib*GIB:
                continue
            if path == active:
                continue
            # Recheck the resolved scope immediately before each nonrecursive unlink.
            self.guard()
            if not plain_path(path) or path.resolve().parent != self.root:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            self.deleted += 1
            total -= size
            free = self.disk_usage(self.root).free
        remaining = self.files()
        total = sum(row[2] for row in remaining)
        free = self.disk_usage(self.root).free
        return {"files": len(remaining), "bytes": total, "deleted_files": self.deleted,
                "free_bytes": free, "can_record": free >= config.min_free_gib*GIB and total < config.max_gib*GIB}

    def close(self):
        if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None


def recording_command(ffmpeg, url, pattern, segment_seconds, *, local_file=False):
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-n"]
    if local_file:
        command += ["-re"]
    else:
        # RTSP uses the demuxer socket timeout; rw_timeout is not accepted here.
        command += ["-rtsp_transport", "tcp", "-timeout", "8000000"]
    command += ["-i", url, "-map", "0:v:0", "-an", "-c:v", "copy", "-f", "segment",
                "-segment_time", str(segment_seconds), "-reset_timestamps", "1",
                "-segment_format", "matroska", "-segment_format_options",
                "cluster_time_limit=1000:flush_packets=1", str(pattern)]
    return command


class CameraRecorder:
    """One owned FFmpeg child, periodic cleanup, retries and free-space guard.

    start/close follow camera-preview lifetime. Short-lived segment overflow is
    possible; polling and segment boundaries are not a filesystem quota.
    """
    def __init__(self, config: RecordingConfig, url, *, local_file=False):
        self.config, self.url = config, url
        self.local_file = local_file
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.info = {"enabled": config.enabled, "state": "starting" if config.enabled else "disabled",
                     "directory": config.directory, "segment_seconds": config.segment_seconds,
                     "retention_days": config.retention_days, "max_gib": config.max_gib,
                     "min_free_gib": config.min_free_gib, "files": 0, "bytes": 0, "deleted_files": 0}
        self.process = self.store = self.prefix = None
        self.thread = None
        if config.enabled:
            self.thread = threading.Thread(target=self._run, name="camera-recorder", daemon=True)
            self.thread.start()

    def status(self):
        with self.lock:
            return dict(self.info)

    def _set(self, **values):
        with self.lock:
            self.info.update(values)

    def _active(self):
        if self.process is None or self.process.poll() is not None:
            return None
        files = [row[0] for row in self.store.files() if row[0].name.startswith(self.prefix+"_")]
        return max(files, key=lambda p: int(VIDEO.fullmatch(p.name)[2])) if files else None

    def _stop_child(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    process.stdin.write(b"q\n"); process.stdin.flush()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill(); process.wait(timeout=2)
        finally:
            if process.stdin:
                process.stdin.close()

    def _run(self):
        try:
            c = self.config
            ffmpeg = Path(c.ffmpeg_path or "")
            if not ffmpeg.is_absolute() or not ffmpeg.is_file():
                raise ValueError("recording_tool_missing")
            if not self.local_file:
                parsed = urlsplit(self.url)
                if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
                    raise ValueError("recording_source_invalid")
            self.store = RecordingStore(c.directory or "")
            cleanup_at = retry_at = stats_at = 0.0
            capacity = None
            while not self.stop_event.is_set():
                now = time.monotonic()
                if self.process is not None and self.process.poll() is not None:
                    self._stop_child()
                    self._set(state="reconnecting", error="recording_stream_interrupted")
                    cleanup_at = 0
                    retry_at = now+3
                free = self.store.disk_usage(self.store.root).free
                active = self._active()
                if now >= stats_at:
                    files = self.store.files()
                    total = sum(row[2] for row in files)
                    self._set(files=len(files), bytes=total, free_bytes=free)
                    if total >= c.max_gib*GIB:
                        cleanup_at = 0
                    stats_at = now+5
                # Frequent low-space check, independent of regular retention sweep.
                if now >= cleanup_at or free < c.min_free_gib*GIB:
                    capacity = self.store.cleanup(c, active)
                    self._set(**{k:v for k,v in capacity.items() if k != "can_record"})
                    cleanup_at = now+c.cleanup_seconds
                if capacity is not None and not capacity["can_record"]:
                    self._stop_child()
                    self._set(state="paused_low_space", error="recording_storage_limit")
                    capacity = self.store.cleanup(c)
                    self._set(**{k:v for k,v in capacity.items() if k != "can_record"})
                    if capacity["can_record"]:
                        retry_at = now+1
                    cleanup_at = min(cleanup_at, now+5)
                elif self.process is None and now >= retry_at:
                    self.prefix = self.store.new_session()
                    pattern = self.store.root/(self.prefix+"_%06d.mkv")
                    self.process = subprocess.Popen(recording_command(ffmpeg, self.url, pattern, c.segment_seconds,
                        local_file=self.local_file), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self._set(state="starting", error=None)
                elif self.process is not None and active is not None and active.stat().st_size > 0:
                    self._set(state="recording", error=None)
                self.stop_event.wait(.5)
        except Exception:
            # Native error messages and arguments may contain camera credentials.
            self._set(state="error", error="local_recording_failed")
        finally:
            try:
                self._stop_child()
                if self.store is not None:
                    capacity = self.store.cleanup(self.config)
                    self._set(**{k:v for k,v in capacity.items() if k != "can_record"})
            except Exception:
                self._set(state="error", error="recording_finalize_failed")
            finally:
                if self.store is not None:
                    self.store.close()
            if self.status()["state"] != "error":
                self._set(state="stopped")

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=6)

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import json
import math

from .help_gesture import HelpConfig
from pathlib import Path


@dataclass
class FallConfig:
    keypoint_confidence: float = 0.55
    angle_degrees: float = 60.0
    upright_angle_degrees: float = 30.0
    min_drop_speed: float = 0.60  # upright bbox heights / second
    min_rotation_speed: float = 120.0  # degrees/second; rapid tilt with a real hip drop
    min_hip_drop: float = 0.18   # upright bbox heights
    min_bbox_ratio: float = 1.15
    min_upright_seconds: float = 0.5
    observation_seconds: float = 1.5
    hold_seconds: float = 0.8
    max_gap_seconds: float = 0.30
    cooldown_seconds: float = 15.0


@dataclass
class FireConfig:
    confidence: float = 0.60
    region_iou: float = 0.30
    hold_seconds: float = 1.0
    min_frames: int = 5
    max_gap_seconds: float = 0.30
    cooldown_seconds: float = 15.0


@dataclass
class TrackingConfig:
    min_iou: float = 0.10
    max_center_distance: float = 0.8
    max_gap_seconds: float = 0.30


@dataclass
class EvidenceConfig:
    pre_seconds: float = 2.0
    post_seconds: float = 2.0
    fps: float = 10.0
    max_width: int = 640
    max_height: int = 480
    max_jpg_bytes: int = 1_500_000
    max_mp4_bytes: int = 8_000_000
    max_active_clips: int = 4


@dataclass
class RecordingConfig:
    enabled: bool = False
    directory: str | None = None
    ffmpeg_path: str | None = None
    segment_seconds: int = 300
    retention_days: float = 3.0
    max_gib: float = 10.0
    min_free_gib: float = 5.0
    cleanup_seconds: float = 60.0


@dataclass
class ModelConfig:
    pose_weights: str | None = None
    flame_weights: str | None = None
    flame_classes: list[str] = field(default_factory=list)
    pose_license: dict[str, str] = field(default_factory=dict)
    flame_license: dict[str, str] = field(default_factory=dict)
    device: str = "cpu"
    cpu_threads: int = 4
    pose_max_width: int = 640
    pose_fall_recovery_enabled: bool = False
    flame_tile_size: int = 1280
    flame_tile_overlap: float = 0.25
    flame_max_tiles: int = 12
    flame_tiling_enabled: bool = False


@dataclass
class Config:
    fall: FallConfig = field(default_factory=FallConfig)
    fire: FireConfig = field(default_factory=FireConfig)
    help: HelpConfig = field(default_factory=HelpConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    models: ModelConfig = field(default_factory=ModelConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    heartbeat_seconds: float = 15.0
    request_timeout_seconds: float = 5.0
    open_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 3.0
    reconnect_initial_seconds: float = 0.5
    reconnect_max_seconds: float = 15.0
    retry_initial_seconds: float = 1.0
    retry_max_seconds: float = 60.0
    max_spool_bytes: int = 512_000_000

    @classmethod
    def load(cls, path: str | None) -> "Config":
        obj = cls()
        if path:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("config must be a JSON object")
            update(obj, data)
        obj.validate()
        return obj

    def validate(self) -> None:
        def walk(obj):
            for f in fields(obj):
                value = getattr(obj, f.name)
                if is_dataclass(value):
                    walk(value)
                elif isinstance(value, bool):
                    if f.name not in {"flame_tiling_enabled", "pose_fall_recovery_enabled", "require_facing_camera"} and not (isinstance(obj, RecordingConfig) and f.name == "enabled"):
                        raise ValueError(f"{f.name} must be numeric, not boolean")
                elif isinstance(value, (int, float)):
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError(f"{f.name} must be finite and positive")
        walk(self)
        self.help.validate()
        if not isinstance(self.recording.enabled, bool):
            raise ValueError("recording.enabled must be boolean")
        if not isinstance(self.recording.segment_seconds, int) or isinstance(self.recording.segment_seconds, bool) or not 1 <= self.recording.segment_seconds <= 3600:
            raise ValueError("recording.segment_seconds must be an integer in 1..3600")
        for value in (self.recording.directory, self.recording.ffmpeg_path):
            if value is not None and (not isinstance(value, str) or not Path(value).is_absolute()):
                raise ValueError("recording paths must be absolute local paths")
        if not isinstance(self.models.flame_tiling_enabled, bool):
            raise ValueError("flame_tiling_enabled must be boolean")
        if not isinstance(self.models.pose_fall_recovery_enabled, bool):
            raise ValueError("pose_fall_recovery_enabled must be boolean")
        if not 256 <= self.models.pose_max_width <= 1920:
            raise ValueError("pose_max_width must be 256..1920")
        if not 320 <= self.models.flame_tile_size <= 1920:
            raise ValueError("flame_tile_size must be 320..1920")
        if not 0 < self.models.flame_tile_overlap <= 0.5:
            raise ValueError("flame_tile_overlap must be in (0, 0.5]")
        if not 1 <= self.models.flame_max_tiles <= 16:
            raise ValueError("flame_max_tiles must be 1..16")
        if self.models.cpu_threads > 32:
            raise ValueError("cpu_threads must be <= 32")
        for value in (self.fall.keypoint_confidence, self.fire.confidence,
                      self.fire.region_iou, self.tracking.min_iou):
            if value > 1:
                raise ValueError("confidence and IoU thresholds must be <= 1")
        if not 0 < self.fall.upright_angle_degrees < self.fall.angle_degrees <= 90:
            raise ValueError("fall angles must satisfy 0 < upright < fallen <= 90")
        if self.fall.hold_seconds > self.fall.observation_seconds:
            raise ValueError("hold_seconds must fit observation_seconds")
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("invalid reconnect bounds")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("invalid retry bounds")
        if self.evidence.pre_seconds + self.evidence.post_seconds > 30:
            raise ValueError("MVP limits clips to 30 seconds")
        if self.evidence.max_width > 1920 or self.evidence.max_height > 1080:
            raise ValueError("MVP maximum evidence resolution is 1920x1080")
        if self.evidence.fps > 30:
            raise ValueError("MVP maximum evidence FPS is 30")
        # Hard ring-memory ceiling, including active clips. The buffers share arrays.
        frames = math.ceil((self.evidence.pre_seconds + self.evidence.post_seconds) * self.evidence.fps) + 4
        if frames * self.evidence.max_width * self.evidence.max_height * 3 * (self.evidence.max_active_clips + 1) > 512_000_000:
            raise ValueError("evidence buffers exceed MVP 512 MB memory budget")


def update(obj, data: dict) -> None:
    known = {f.name for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown config key: {key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be an object")
            update(current, value)
        else:
            # Reject accidental string thresholds and float counters early.
            if isinstance(current, bool) and not isinstance(value, bool):
                raise ValueError(f"{key} must be boolean")
            if isinstance(current, int) and not isinstance(current, bool) and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{key} must be an integer")
            if isinstance(current, float) and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ValueError(f"{key} must be numeric")
            if isinstance(current, list) and (not isinstance(value, list) or not all(isinstance(x, str) for x in value)):
                raise ValueError(f"{key} must be a string list")
            if isinstance(current, dict) and (not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())):
                raise ValueError(f"{key} must map strings to strings")
            if current is None and value is not None and not isinstance(value, str):
                raise ValueError(f"{key} must be a local path or null")
            if isinstance(current, str) and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            setattr(obj, key, value)

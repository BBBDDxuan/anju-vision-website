from __future__ import annotations

from pathlib import Path

from .config import ModelConfig
from .types import Flame, Pose
from .flame_tiles import flame_views, merge_flames


class ModelConfigurationError(ValueError):
    pass


def local_weight(value: str | None, name: str) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".pt":
        raise ModelConfigurationError(f"{name} requires an existing absolute local .pt file")
    return path


class LocalModels:
    """Explicit local YOLO .pt only; no named model/download fallback."""

    def __init__(self, config: ModelConfig, factory=None):
        self.config = config
        self.pose_frames = 0
        self.pose_predict_calls = 0
        self.pose_recovery_frames = 0
        pose_path = local_weight(config.pose_weights, "pose_weights")
        flame_path = local_weight(config.flame_weights, "flame_weights")
        if not pose_path and not flame_path:
            raise ModelConfigurationError("no local weights configured; use --source mock for simulation, or configure pose_weights/flame_weights")
        for path, metadata, kind in ((pose_path, config.pose_license, "pose"),
                                     (flame_path, config.flame_license, "flame")):
            if path:
                for key in ("name", "source", "license", "usage_review"):
                    if not metadata.get(key, "").strip():
                        raise ModelConfigurationError(f"{kind}_license.{key} is required")
        if flame_path:
            if not config.flame_classes or len(set(config.flame_classes)) != len(config.flame_classes):
                raise ModelConfigurationError("flame_classes must explicitly name unique flame classes")
            # Metadata records a human review; it does not grant a license.
            if Path(flame_path).name.lower().startswith(("yolov8n.", "yolo11n.", "yolo26n.")):
                raise ModelConfigurationError("generic COCO weights cannot serve as flame weights")
        if (pose_path or flame_path) and factory is None:
            try:
                from .runtime_env import configure_model_runtime
                configure_model_runtime()
                import torch
                torch.set_num_threads(config.cpu_threads)
                from ultralytics import YOLO
            except ImportError as exc:
                raise ModelConfigurationError("install the optional pose extra to load local models") from exc
            factory = YOLO
        try:
            self.pose = factory(str(pose_path), task="pose") if pose_path else None
            self.flame = factory(str(flame_path), task="detect") if flame_path else None
        except Exception:
            raise ModelConfigurationError("local model could not be loaded; check trusted weights and optional dependencies") from None
        if self.pose is not None:
            if getattr(self.pose, "task", None) != "pose":
                raise ModelConfigurationError("pose_weights is not a pose model")
            shape = getattr(getattr(self.pose, "model", None), "yaml", {}).get("kpt_shape")
            if shape is None or list(shape) != [17, 3]:
                raise ModelConfigurationError("pose model must use COCO 17 keypoints with confidence")
        if self.flame is not None:
            if getattr(self.flame, "task", None) != "detect":
                raise ModelConfigurationError("flame_weights must be a detection model")
            names = self.flame.names
            available = set(names.values() if isinstance(names, dict) else names)
            if not set(config.flame_classes).issubset(available):
                raise ModelConfigurationError("configured flame classes are absent from model labels")
            # Reject standard COCO regardless of renamed weight files.
            if {"person", "car", "chair", "tv", "dog", "bicycle"}.issubset(available):
                raise ModelConfigurationError("COCO detection models are not flame models")

    def infer(self, image):
        return self.infer_pose(image), self.infer_flames(image)

    def infer_pose(self, image):
        self.pose_frames += 1
        poses = self._infer_pose(image)
        if self.pose is not None and self.config.pose_fall_recovery_enabled:
            from .pose_recovery import needs_recovery, recover_poses
            if needs_recovery(poses):
                self.pose_recovery_frames += 1
                poses = recover_poses(image, poses, self._infer_pose)
        return poses

    def _infer_pose(self, image):
        poses = []
        height, width = image.shape[:2]
        if self.pose is not None:
            pose_image = image
            if width > self.config.pose_max_width:
                import cv2
                target_width = self.config.pose_max_width
                pose_image = cv2.resize(image, (target_width, max(1, round(height*target_width/width))))
            sx, sy = width/pose_image.shape[1], height/pose_image.shape[0]
            self.pose_predict_calls += 1
            result = self.pose.predict(pose_image, device=self.config.device, verbose=False)[0]
            if result.keypoints is not None and result.boxes is not None:
                boxes = result.boxes.xyxy.cpu().numpy()
                scores = result.boxes.conf.cpu().numpy()
                points = result.keypoints.data.cpu().numpy()
                for box, score, kp in zip(boxes, scores, points):
                    kp = kp.copy()
                    kp[:, 0] *= sx
                    kp[:, 1] *= sy
                    mapped = (float(box[0])*sx, float(box[1])*sy, float(box[2])*sx, float(box[3])*sy)
                    poses.append(Pose(mapped, kp, float(score)))
        return poses

    def infer_flames(self, image):
        flames = []
        height, width = image.shape[:2]
        if self.flame is not None:
            # Keep full-frame detection to retain context for larger fires.
            result = self.flame.predict(image, device=self.config.device, verbose=False)[0]
            flames.extend(self._flames(result, 0, 0, width, height))
            if self.config.flame_tiling_enabled:
                views = flame_views(image, self.config.flame_tile_size,
                    self.config.flame_tile_overlap, self.config.flame_max_tiles)
                # Batch at most four views to bound GPU activation memory.
                for start in range(0, len(views), 4):
                    batch = views[start:start+4]
                    results = self.flame.predict([v[0] for v in batch], imgsz=640,
                        device=self.config.device, verbose=False)
                    for result, (_, x, y) in zip(results, batch):
                        flames.extend(self._flames(result, x, y, width, height))
                flames = merge_flames(flames)
        return flames

    def _flames(self, result, x, y, width, height):
        detections = []
        if result.boxes is None:
            return detections
        for box, score, cls in zip(result.boxes.xyxy.cpu().numpy(),
                                   result.boxes.conf.cpu().numpy(),
                                   result.boxes.cls.cpu().numpy()):
            label = result.names[int(cls)]
            if label in self.config.flame_classes:
                mapped = (max(0., min(float(width), float(box[0])+x)),
                          max(0., min(float(height), float(box[1])+y)),
                          max(0., min(float(width), float(box[2])+x)),
                          max(0., min(float(height), float(box[3])+y)))
                detections.append(Flame(mapped, float(score), label))
        return detections

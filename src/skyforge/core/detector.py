"""Object detection pipeline — YOLO-based detection on aerial video frames."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


@dataclass
class DetectionResult:
    """A single detected object in a frame."""

    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]  # normalized x1,y1,x2,y2 (0-1)
    bbox_pixels: tuple[int, int, int, int]  # absolute pixel coords


@dataclass
class FrameDetections:
    """All detections for a single sampled frame."""

    frame_idx: int
    timestamp_s: float
    detections: list[DetectionResult]


@dataclass
class VideoDetections:
    """Complete detection results for a video file."""

    source: str  # str path for JSON serialization
    model: str
    total_frames_sampled: int
    frames: list[FrameDetections] = field(default_factory=list)
    unique_classes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)


# ============================================================================
# ObjectDetector — lazy-loaded YOLO wrapper
# ============================================================================


class ObjectDetector:
    """YOLO-based object detector with lazy model loading.

    Delays importing ultralytics/torch until first use so the module
    can be imported without heavy ML dependencies installed.

    Args:
        model_name: YOLO model weight file (e.g. "yolov8n.pt").
        confidence: Minimum detection confidence threshold.
        iou_threshold: IoU threshold for non-max suppression.
        device: Compute device ("mps", "cuda", "cpu", or None for auto).
    """

    def __init__(
        self,
        model_name: str = "yolov8n.pt",
        confidence: float = 0.25,
        iou_threshold: float = 0.45,
        device: str | None = None,
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(model_name)
        self.confidence = confidence
        self.iou_threshold = iou_threshold
        self.device = device or _auto_device()

    def detect_frame(self, frame: np.ndarray) -> list[DetectionResult]:
        """Run detection on a single BGR frame.

        Args:
            frame: OpenCV BGR image array.

        Returns:
            List of DetectionResult for all objects found.
        """
        results = self.model(
            frame,
            conf=self.confidence,
            iou=self.iou_threshold,
            device=self.device,
            verbose=False,
        )

        detections: list[DetectionResult] = []
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return detections

        names = self.model.names
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            conf = float(boxes.conf[i].item())
            # Normalized coords (0-1)
            xn1, yn1, xn2, yn2 = boxes.xyxyn[i].tolist()
            # Absolute pixel coords
            xp1, yp1, xp2, yp2 = boxes.xyxy[i].tolist()

            detections.append(
                DetectionResult(
                    class_name=names.get(cls_id, f"class_{cls_id}"),
                    confidence=round(conf, 4),
                    bbox=(round(xn1, 4), round(yn1, 4), round(xn2, 4), round(yn2, 4)),
                    bbox_pixels=(int(xp1), int(yp1), int(xp2), int(yp2)),
                )
            )

        return detections

    def get_class_names(self) -> list[str]:
        """Return all class names the model can detect."""
        return list(self.model.names.values())


# ============================================================================
# Video-level detection
# ============================================================================


def detect_video(
    video_path: Path,
    detector: ObjectDetector,
    sample_interval: float = 2.0,
    classes_filter: list[str] | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> VideoDetections:
    """Run object detection across sampled frames of a video.

    Args:
        video_path: Path to input video file.
        detector: Configured ObjectDetector instance.
        sample_interval: Seconds between sampled frames.
        classes_filter: If provided, keep only detections matching these class names.
        on_progress: Optional callback(frame_idx, total_frames, detections_count).

    Returns:
        VideoDetections with per-frame results and aggregate class counts.
    """
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = int(fps * sample_interval)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    class_counter: Counter[str] = Counter()
    frames: list[FrameDetections] = []
    frames_sampled = 0
    frame_idx = 0

    filter_set = set(classes_filter) if classes_filter else None

    while frame_idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break

        timestamp_s = frame_idx / fps
        detections = detector.detect_frame(frame)

        # Apply class filter if specified
        if filter_set:
            detections = [d for d in detections if d.class_name in filter_set]

        for d in detections:
            class_counter[d.class_name] += 1

        frames.append(
            FrameDetections(
                frame_idx=frame_idx,
                timestamp_s=round(timestamp_s, 3),
                detections=detections,
            )
        )
        frames_sampled += 1

        if on_progress is not None:
            on_progress(frame_idx, total_frames, len(detections))

        frame_idx += frame_interval

    cap.release()

    return VideoDetections(
        source=str(video_path),
        model=str(detector.model.model_name),
        total_frames_sampled=frames_sampled,
        frames=frames,
        unique_classes=dict(class_counter),
    )


# ============================================================================
# Persistence
# ============================================================================


def save_detections(detections: VideoDetections, output: Path) -> None:
    """Write detection results to a JSON file.

    Args:
        detections: VideoDetections to serialize.
        output: Destination JSON path (parent dirs created automatically).
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(detections.to_dict(), indent=2))


def load_detections(path: Path) -> dict:
    """Load detection results from a JSON file.

    Args:
        path: Path to a *_detections.json file.

    Returns:
        Parsed dict of detection data.
    """
    return json.loads(path.read_text())


# ============================================================================
# Helpers
# ============================================================================


def _auto_device() -> str:
    """Detect the best available compute device for inference.

    Prefers Apple Silicon MPS, then CUDA, falls back to CPU.
    """
    try:
        import torch

        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"

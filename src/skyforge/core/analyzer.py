"""Video analysis pipeline — scene detection, keyframes, blur, motion, audio analysis."""

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np


@dataclass
class SceneChange:
    """A detected scene change point."""
    timestamp: float
    score: float


@dataclass
class AudioPeak:
    """A detected audio peak."""
    timestamp: float
    amplitude: float


@dataclass
class FrameAnalysis:
    """Analysis results for a single sampled frame."""
    timestamp: float
    blur_score: float  # higher = sharper
    brightness: float  # 0-255
    contrast: float
    motion_score: float  # vs previous frame
    is_dark: bool
    is_overexposed: bool
    is_blurry: bool


@dataclass
class VideoAnalysis:
    """Complete analysis results for a single video file."""
    source_file: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    codec: str = ""
    scene_changes: list[SceneChange] = field(default_factory=list)
    frame_analyses: list[FrameAnalysis] = field(default_factory=list)
    audio_peaks: list[AudioPeak] = field(default_factory=list)
    avg_blur: float = 0.0
    avg_brightness: float = 0.0
    avg_motion: float = 0.0
    dark_ratio: float = 0.0
    blurry_ratio: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# Scene change detection (FFmpeg scdet filter)
# ============================================================================

def detect_scene_changes(video_path: Path, threshold: float = 27.0) -> list[SceneChange]:
    """Detect scene changes using PySceneDetect (ContentDetector).

    Falls back to FFmpeg scdet if PySceneDetect is unavailable.
    """
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector

        video = open_video(str(video_path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video, show_progress=False)

        scene_list = scene_manager.get_scene_list()
        changes = []
        for start, _end in scene_list:
            changes.append(SceneChange(
                timestamp=start.get_seconds(),
                score=1.0,  # PySceneDetect doesn't expose per-cut scores directly
            ))
        return changes

    except ImportError:
        # Fallback to FFmpeg scdet
        return _detect_scenes_ffmpeg(video_path, threshold=0.3)


def _detect_scenes_ffmpeg(video_path: Path, threshold: float = 0.3) -> list[SceneChange]:
    """Fallback scene detection using FFmpeg's scdet filter."""
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(video_path),
        "-vf", f"scdet=threshold={threshold}:sc_pass=1",
        "-an", "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    stderr = result.stderr

    changes = []
    pattern = re.compile(r"lavfi\.scd\.time:\s*([\d.]+).*?lavfi\.scd\.score:\s*([\d.]+)")
    for match in pattern.finditer(stderr):
        changes.append(SceneChange(
            timestamp=float(match.group(1)),
            score=float(match.group(2)),
        ))

    return changes


# ============================================================================
# Contact sheet / keyframe extraction
# ============================================================================

def extract_contact_sheet(
    video_path: Path,
    output_dir: Path,
    interval: float = 5.0,
    width: int = 320,
) -> list[Path]:
    """Extract keyframes at regular intervals as a contact sheet."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%04d.jpg")

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval},scale={width}:-1",
        "-q:v", "3",
        pattern,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    frames = sorted(output_dir.glob("frame_*.jpg"))
    return frames


def extract_contact_sheet_montage(
    video_path: Path,
    output_path: Path,
    cols: int = 5,
    interval: float = 5.0,
    thumb_width: int = 320,
) -> Path | None:
    """Create a single montage image of keyframes using FFmpeg tile filter."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get duration to calculate rows
    duration = _get_duration(video_path)
    if duration <= 0:
        return None

    total_frames = int(duration / interval) + 1
    rows = (total_frames + cols - 1) // cols

    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval},scale={thumb_width}:-1,tile={cols}x{rows}",
        "-frames:v", "1",
        "-q:v", "3",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if output_path.exists():
        return output_path
    return None


# ============================================================================
# Audio analysis
# ============================================================================

def analyze_audio(video_path: Path, output_dir: Path) -> tuple[list[AudioPeak], Path | None]:
    """Analyze audio: extract waveform image and detect peaks."""
    output_dir.mkdir(parents=True, exist_ok=True)
    peaks = []

    # Check if video has audio
    has_audio = _has_audio(video_path)
    if not has_audio:
        return peaks, None

    # Generate waveform image
    waveform_path = output_dir / "waveform.png"
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(video_path),
        "-filter_complex", "showwavespic=s=1200x200:colors=cyan",
        "-frames:v", "1",
        str(waveform_path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Detect audio peaks using astats
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(video_path),
        "-af", "silencedetect=noise=-30dB:d=1",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Parse silence detection to find non-silent (active) regions
    silence_starts = []
    silence_ends = []
    for line in result.stderr.split("\n"):
        if "silence_start:" in line:
            m = re.search(r"silence_start:\s*([\d.]+)", line)
            if m:
                silence_starts.append(float(m.group(1)))
        elif "silence_end:" in line:
            m = re.search(r"silence_end:\s*([\d.]+)", line)
            if m:
                silence_ends.append(float(m.group(1)))

    # Simple peak detection: midpoints of non-silent regions
    duration = _get_duration(video_path)
    if not silence_starts:
        # No silence detected = audio throughout
        peaks.append(AudioPeak(timestamp=duration / 2, amplitude=0.8))
    else:
        # Active regions are gaps between silence
        prev_end = 0.0
        for ss in silence_starts:
            if ss > prev_end + 1.0:
                peaks.append(AudioPeak(
                    timestamp=(prev_end + ss) / 2,
                    amplitude=0.7,
                ))
            prev_end = ss
        # Check for silence_ends
        for se in silence_ends:
            prev_end = max(prev_end, se)
        if duration > prev_end + 1.0:
            peaks.append(AudioPeak(
                timestamp=(prev_end + duration) / 2,
                amplitude=0.7,
            ))

    wf = waveform_path if waveform_path.exists() else None
    return peaks, wf


# ============================================================================
# Frame-level analysis (OpenCV)
# ============================================================================

def analyze_frames(
    video_path: Path,
    sample_interval: float = 1.0,
    blur_threshold: float = 80.0,
    dark_threshold: float = 40.0,
    bright_threshold: float = 230.0,
) -> list[FrameAnalysis]:
    """Sample frames from video and analyze blur, brightness, motion."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = int(fps * sample_interval)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    analyses = []
    prev_gray = None
    frame_idx = 0

    while frame_idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            break

        timestamp = frame_idx / fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Blur detection (Laplacian variance — higher = sharper)
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # Brightness
        brightness = float(np.mean(gray))

        # Contrast (std dev of intensity)
        contrast = float(np.std(gray))

        # Motion (frame difference)
        motion_score = 0.0
        if prev_gray is not None:
            diff = cv2.absdiff(prev_gray, gray)
            motion_score = float(np.mean(diff))

        is_dark = bool(brightness < dark_threshold)
        is_overexposed = bool(brightness > bright_threshold)
        is_blurry = bool(blur_score < blur_threshold)

        analyses.append(FrameAnalysis(
            timestamp=timestamp,
            blur_score=round(blur_score, 2),
            brightness=round(brightness, 2),
            contrast=round(contrast, 2),
            motion_score=round(motion_score, 2),
            is_dark=is_dark,
            is_overexposed=is_overexposed,
            is_blurry=is_blurry,
        ))

        prev_gray = gray
        frame_idx += frame_interval

    cap.release()
    return analyses


# ============================================================================
# Full video analysis orchestrator
# ============================================================================

def analyze_video(
    video_path: Path, output_dir: Path, sample_interval: float = 1.0,
) -> VideoAnalysis:
    """Run complete analysis on a single video file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis = VideoAnalysis(source_file=str(video_path))

    # Get basic metadata via ffprobe
    meta = _get_metadata(video_path)
    analysis.duration = meta.get("duration", 0.0)
    analysis.width = meta.get("width", 0)
    analysis.height = meta.get("height", 0)
    analysis.fps = meta.get("fps", 0.0)
    analysis.has_audio = meta.get("has_audio", False)
    analysis.codec = meta.get("codec", "")

    # Save ffprobe metadata dump
    _dump_ffprobe(video_path, output_dir / "ffprobe.json")

    # Scene change detection
    analysis.scene_changes = detect_scene_changes(video_path)

    # Contact sheet
    extract_contact_sheet(video_path, output_dir / "keyframes", interval=5.0)
    extract_contact_sheet_montage(video_path, output_dir / "contact_sheet.jpg", interval=5.0)

    # Audio analysis
    analysis.audio_peaks, _ = analyze_audio(video_path, output_dir)

    # Frame-level analysis
    analysis.frame_analyses = analyze_frames(video_path, sample_interval=sample_interval)

    # Compute aggregates
    if analysis.frame_analyses:
        n = len(analysis.frame_analyses)
        analysis.avg_blur = round(sum(f.blur_score for f in analysis.frame_analyses) / n, 2)
        analysis.avg_brightness = round(sum(f.brightness for f in analysis.frame_analyses) / n, 2)
        analysis.avg_motion = round(sum(f.motion_score for f in analysis.frame_analyses) / n, 2)
        analysis.dark_ratio = round(sum(1 for f in analysis.frame_analyses if f.is_dark) / n, 3)
        analysis.blurry_ratio = round(sum(1 for f in analysis.frame_analyses if f.is_blurry) / n, 3)

    # Save analysis JSON
    analysis_json = output_dir / "analysis.json"
    analysis_json.write_text(json.dumps(analysis.to_dict(), indent=2))

    return analysis


# ============================================================================
# Helpers
# ============================================================================

def _get_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio(video_path: Path) -> bool:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return len(result.stdout.strip()) > 0


def _get_metadata(video_path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate,duration",
        "-show_entries", "format=duration",
        "-of", "json", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    meta = {}
    streams = data.get("streams", [])
    if streams:
        vs = streams[0]
        meta["codec"] = vs.get("codec_name", "")
        meta["width"] = int(vs.get("width", 0))
        meta["height"] = int(vs.get("height", 0))
        fps_str = vs.get("r_frame_rate", "0/1")
        if "/" in fps_str:
            num, den = fps_str.split("/")
            meta["fps"] = float(num) / max(float(den), 1)
        else:
            meta["fps"] = float(fps_str)
        dur = vs.get("duration") or data.get("format", {}).get("duration")
        if dur:
            meta["duration"] = float(dur)

    meta["has_audio"] = _has_audio(video_path)
    return meta


def _dump_ffprobe(video_path: Path, output_path: Path) -> None:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output_path.write_text(result.stdout)

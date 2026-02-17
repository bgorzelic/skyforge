"""Video quality analysis — frame scoring, scene detection, and audio analysis.

Extracted from Skyforge's core/analyzer.py for FlightDeck integration.
Place in FlightDeck at: processing/src/quality_analyzer.py

This module runs synchronously. Wrap calls in asyncio.to_thread() when
integrating with FlightDeck's async pipeline workers. FlightDeck workers
should download the asset from S3 to a temp path before calling these functions.

Dependencies: opencv-python-headless, numpy, scenedetect, ffmpeg (system binary)
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np

from flightdeck_contrib.schemas.quality import (
    AudioAnalysisResult,
    AudioPeak,
    FrameQualityMetrics,
    SceneChange,
    SilenceRegion,
    VideoQualityReport,
)


class QualityAnalyzer:
    """Analyzes video assets for frame quality, scene structure, and audio.

    Designed to be instantiated once per worker process. All methods operate
    on local file paths — callers are responsible for downloading from S3 first.

    Example usage in FlightDeck worker::

        async def analyze_asset(asset_id: str, s3_path: str) -> VideoQualityReport:
            tmp = await download_s3(s3_path)
            report = await asyncio.to_thread(
                QualityAnalyzer().analyze_video, tmp, tmp.parent / "analysis"
            )
            return report
    """

    def analyze_frames(
        self,
        video_path: Path,
        sample_interval: float = 1.0,
        blur_threshold: float = 80.0,
        dark_threshold: float = 40.0,
        bright_threshold: float = 230.0,
    ) -> list[FrameQualityMetrics]:
        """Sample frames from a video and compute per-frame quality metrics.

        Uses OpenCV to read frames at ``sample_interval`` second intervals.
        Computes Laplacian variance for sharpness, mean pixel intensity for
        brightness, standard deviation for contrast, and frame-diff for motion.

        Args:
            video_path: Local path to the video file.
            sample_interval: Seconds between sampled frames.
            blur_threshold: Laplacian variance below this is flagged as blurry.
            dark_threshold: Mean brightness below this is flagged as dark.
            bright_threshold: Mean brightness above this is flagged as overexposed.

        Returns:
            List of FrameQualityMetrics, one per sampled frame.
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_interval = int(fps * sample_interval)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        analyses: list[FrameQualityMetrics] = []
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

            quality_score, _ = self.score_frame(
                FrameQualityMetrics(
                    timestamp=timestamp,
                    blur_score=blur_score,
                    brightness=brightness,
                    contrast=contrast,
                    motion_score=motion_score,
                    is_dark=is_dark,
                    is_overexposed=is_overexposed,
                    is_blurry=is_blurry,
                    quality_score=0.0,
                ),
                blur_threshold=blur_threshold,
                dark_threshold=dark_threshold,
            )

            analyses.append(
                FrameQualityMetrics(
                    timestamp=timestamp,
                    blur_score=round(blur_score, 2),
                    brightness=round(brightness, 2),
                    contrast=round(contrast, 2),
                    motion_score=round(motion_score, 2),
                    is_dark=is_dark,
                    is_overexposed=is_overexposed,
                    is_blurry=is_blurry,
                    quality_score=round(quality_score, 3),
                )
            )

            prev_gray = gray
            frame_idx += frame_interval

        cap.release()
        return analyses

    def detect_scene_changes(
        self,
        video_path: Path,
        threshold: float = 27.0,
    ) -> list[SceneChange]:
        """Detect scene changes using PySceneDetect with FFmpeg fallback.

        Attempts PySceneDetect ContentDetector first. Falls back to FFmpeg's
        scdet filter if PySceneDetect is not installed.

        Args:
            video_path: Local path to the video file.
            threshold: Detection sensitivity. Higher = fewer cuts detected.
                PySceneDetect uses 27.0 as default; FFmpeg scdet uses 0.3.

        Returns:
            List of SceneChange with timestamp and confidence score.
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
                changes.append(
                    SceneChange(
                        timestamp=start.get_seconds(),
                        score=1.0,  # PySceneDetect doesn't expose per-cut scores
                    )
                )
            return changes

        except ImportError:
            return self._detect_scenes_ffmpeg(video_path, threshold=0.3)

    def analyze_audio(
        self,
        video_path: Path,
        output_dir: Path,
    ) -> AudioAnalysisResult:
        """Analyze audio track: detect silence regions, peaks, and generate waveform.

        Uses FFmpeg silencedetect to find silent regions, then derives active
        (peak) regions as the inverse. Optionally generates a waveform image
        that can be uploaded to S3 and referenced in the result.

        Args:
            video_path: Local path to the video file.
            output_dir: Directory for the waveform image output.

        Returns:
            AudioAnalysisResult with silence regions, peaks, and waveform path.
        """
        result = AudioAnalysisResult()
        result.has_audio = _has_audio(video_path)
        if not result.has_audio:
            return result

        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate waveform image
        waveform_path = output_dir / "waveform.png"
        wf_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(video_path),
            "-filter_complex",
            "showwavespic=s=1200x200:colors=cyan",
            "-frames:v",
            "1",
            str(waveform_path),
        ]
        subprocess.run(wf_cmd, capture_output=True, text=True, timeout=120)

        # Detect silence regions
        silence_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(video_path),
            "-af",
            "silencedetect=noise=-30dB:d=1",
            "-f",
            "null",
            "-",
        ]
        silence_result = subprocess.run(
            silence_cmd, capture_output=True, text=True, timeout=120
        )

        silence_starts: list[float] = []
        silence_ends: list[float] = []
        for line in silence_result.stderr.split("\n"):
            if "silence_start:" in line:
                m = re.search(r"silence_start:\s*([\d.]+)", line)
                if m:
                    silence_starts.append(float(m.group(1)))
            elif "silence_end:" in line:
                m = re.search(r"silence_end:\s*([\d.]+)", line)
                if m:
                    silence_ends.append(float(m.group(1)))

        # Build SilenceRegion pairs
        for i, start in enumerate(silence_starts):
            end = silence_ends[i] if i < len(silence_ends) else start + 1.0
            result.silence_regions.append(SilenceRegion(start=start, end=end))

        # Derive audio peaks as midpoints of non-silent regions
        duration = _get_duration(video_path)
        if not silence_starts:
            # No silence detected — audio throughout
            result.audio_peaks.append(AudioPeak(timestamp=duration / 2, amplitude=0.8))
        else:
            prev_end = 0.0
            for ss in silence_starts:
                if ss > prev_end + 1.0:
                    result.audio_peaks.append(
                        AudioPeak(timestamp=(prev_end + ss) / 2, amplitude=0.7)
                    )
                prev_end = ss
            for se in silence_ends:
                prev_end = max(prev_end, se)
            if duration > prev_end + 1.0:
                result.audio_peaks.append(
                    AudioPeak(timestamp=(prev_end + duration) / 2, amplitude=0.7)
                )

        # waveform_s3_path is populated by the caller after uploading to S3
        if waveform_path.exists():
            result.waveform_s3_path = str(waveform_path)

        return result

    def extract_contact_sheet(
        self,
        video_path: Path,
        output_path: Path,
        cols: int = 5,
        interval: float = 5.0,
        thumb_width: int = 320,
    ) -> Path | None:
        """Create a single montage image of keyframes using FFmpeg tile filter.

        Generates one JPEG containing a grid of thumbnails sampled at
        ``interval`` second intervals. Useful for visual QA of assets.

        Args:
            video_path: Local path to the video file.
            output_path: Destination path for the montage JPEG.
            cols: Number of columns in the tile grid.
            interval: Seconds between thumbnails.
            thumb_width: Width of each thumbnail in pixels.

        Returns:
            Path to the generated montage, or None on failure.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        duration = _get_duration(video_path)
        if duration <= 0:
            return None

        total_frames = int(duration / interval) + 1
        rows = (total_frames + cols - 1) // cols

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{interval},scale={thumb_width}:-1,tile={cols}x{rows}",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if output_path.exists():
            return output_path
        return None

    @staticmethod
    def score_frame(
        metrics: FrameQualityMetrics,
        blur_threshold: float = 80.0,
        dark_threshold: float = 40.0,
    ) -> tuple[float, list[str]]:
        """Score a single frame 0-1 and return descriptive reason tags.

        Scoring algorithm ported exactly from Skyforge's selector.py:197-246.
        Penalties are applied for blur, darkness, overexposure, and low contrast.
        Bonuses reward moderate motion and good exposure.

        Args:
            metrics: Per-frame metrics from analyze_frames().
            blur_threshold: Laplacian variance below this is considered blurry.
            dark_threshold: Mean brightness below this is considered dark.

        Returns:
            Tuple of (score 0.0-1.0, list of string tags).
        """
        score = 1.0
        tags: list[str] = []

        # Penalize blur
        if metrics.is_blurry:
            score -= 0.5
            tags.append("blurry")
        elif metrics.blur_score > blur_threshold * 3:
            tags.append("sharp")

        # Penalize darkness
        if metrics.is_dark:
            score -= 0.6
            tags.append("too_dark")
        elif metrics.brightness < 60:
            score -= 0.2
            tags.append("dim")

        # Penalize overexposure
        if metrics.is_overexposed:
            score -= 0.4
            tags.append("overexposed")

        # Low contrast (lens covered, fog, etc.)
        if metrics.contrast < 15:
            score -= 0.5
            tags.append("low_contrast")

        # Reward moderate motion (interesting content)
        if 2.0 < metrics.motion_score < 20.0:
            score += 0.1
            tags.append("good_motion")
        elif metrics.motion_score < 0.5:
            tags.append("static")
        elif metrics.motion_score > 30.0:
            score -= 0.2
            tags.append("shaky")

        # Reward good exposure
        if 80 < metrics.brightness < 180 and metrics.contrast > 30:
            score += 0.1
            tags.append("well_exposed")

        return max(0.0, min(1.0, score)), tags

    def analyze_video(
        self,
        video_path: Path,
        output_dir: Path,
        sample_interval: float = 1.0,
        asset_id: str = "",
    ) -> VideoQualityReport:
        """Run complete quality analysis on a single video file.

        Orchestrates scene detection, contact sheet extraction, audio analysis,
        and frame-level analysis into a single VideoQualityReport. Writes an
        analysis.json to output_dir for persistence.

        Args:
            video_path: Local path to the video file.
            output_dir: Directory for analysis artifacts (JSON, contact sheet, waveform).
            sample_interval: Seconds between sampled frames.
            asset_id: FlightDeck asset UUID; defaults to the filename stem if empty.

        Returns:
            VideoQualityReport with all analysis results populated.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        effective_asset_id = asset_id or video_path.stem
        report = VideoQualityReport(
            asset_id=effective_asset_id,
            source_file=str(video_path),
        )

        # Basic metadata via ffprobe
        meta = _get_metadata(video_path)
        report.duration = meta.get("duration", 0.0)
        report.width = meta.get("width", 0)
        report.height = meta.get("height", 0)
        report.fps = meta.get("fps", 0.0)
        report.has_audio = meta.get("has_audio", False)
        report.codec = meta.get("codec", "")

        # Dump full ffprobe output for debugging
        _dump_ffprobe(video_path, output_dir / "ffprobe.json")

        # Scene change detection
        report.scene_changes = self.detect_scene_changes(video_path)

        # Contact sheet
        self.extract_contact_sheet(
            video_path,
            output_dir / "contact_sheet.jpg",
            interval=5.0,
        )

        # Audio analysis
        report.audio_analysis = self.analyze_audio(video_path, output_dir)

        # Frame-level analysis
        report.frame_analyses = self.analyze_frames(
            video_path, sample_interval=sample_interval
        )

        # Compute aggregates
        if report.frame_analyses:
            n = len(report.frame_analyses)
            report.avg_blur = round(
                sum(f.blur_score for f in report.frame_analyses) / n, 2
            )
            report.avg_brightness = round(
                sum(f.brightness for f in report.frame_analyses) / n, 2
            )
            report.avg_motion = round(
                sum(f.motion_score for f in report.frame_analyses) / n, 2
            )
            report.dark_ratio = round(
                sum(1 for f in report.frame_analyses if f.is_dark) / n, 3
            )
            report.blurry_ratio = round(
                sum(1 for f in report.frame_analyses if f.is_blurry) / n, 3
            )

        # Persist analysis JSON
        (output_dir / "analysis.json").write_text(
            json.dumps(report.model_dump(), indent=2)
        )

        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_scenes_ffmpeg(
        self, video_path: Path, threshold: float = 0.3
    ) -> list[SceneChange]:
        """Fallback scene detection using FFmpeg's scdet filter."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(video_path),
            "-vf",
            f"scdet=threshold={threshold}:sc_pass=1",
            "-an",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        stderr = result.stderr

        changes: list[SceneChange] = []
        pattern = re.compile(
            r"lavfi\.scd\.time:\s*([\d.]+).*?lavfi\.scd\.score:\s*([\d.]+)"
        )
        for match in pattern.finditer(stderr):
            changes.append(
                SceneChange(
                    timestamp=float(match.group(1)),
                    score=float(match.group(2)),
                )
            )
        return changes


# ============================================================================
# Module-level helpers (not part of the public API)
# ============================================================================


def _get_duration(video_path: Path) -> float:
    """Return video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio(video_path: Path) -> bool:
    """Return True if the video file contains at least one audio stream."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return len(result.stdout.strip()) > 0


def _get_metadata(video_path: Path) -> dict:
    """Extract basic video metadata via ffprobe. Returns a flat dict."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    meta: dict = {}
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
    """Write full ffprobe JSON output to a file for debugging."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    output_path.write_text(result.stdout)

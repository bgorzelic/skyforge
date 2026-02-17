"""Quality analysis schemas for FlightDeck integration.

Drop-in replacement for Skyforge's dataclass-based models.
Place in FlightDeck at: shared/src/schemas/quality.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FrameQualityMetrics(BaseModel):
    """Per-frame quality metrics from OpenCV analysis."""

    timestamp: float = Field(description="Frame timestamp in seconds")
    blur_score: float = Field(description="Laplacian variance - higher means sharper")
    brightness: float = Field(ge=0, le=255, description="Mean pixel intensity")
    contrast: float = Field(ge=0, description="Std dev of pixel intensity")
    motion_score: float = Field(ge=0, description="Frame difference magnitude vs previous frame")
    is_dark: bool = Field(default=False, description="Brightness below dark threshold")
    is_overexposed: bool = Field(
        default=False, description="Brightness above overexposure threshold"
    )
    is_blurry: bool = Field(default=False, description="Blur score below sharpness threshold")
    quality_score: float = Field(ge=0, le=1, description="Composite quality score 0-1")


class SceneChange(BaseModel):
    """A detected scene change point."""

    timestamp: float
    score: float = Field(ge=0, le=1)


class AudioPeak(BaseModel):
    """A detected audio activity region."""

    timestamp: float
    amplitude: float = Field(ge=0, le=1)


class SilenceRegion(BaseModel):
    """A detected silence region in audio."""

    start: float
    end: float


class AudioAnalysisResult(BaseModel):
    """Audio analysis results for a video asset."""

    has_audio: bool = False
    silence_regions: list[SilenceRegion] = Field(default_factory=list)
    audio_peaks: list[AudioPeak] = Field(default_factory=list)
    waveform_s3_path: str | None = None


class SegmentQualityReport(BaseModel):
    """Quality metrics and tags for a video segment."""

    confidence: float = Field(ge=0, le=1, description="Composite quality confidence")
    reason_tags: list[str] = Field(
        default_factory=list,
        description="Descriptive tags like 'establishing_shot', 'slow_pan'",
    )
    notes: str = Field(default="", description="Human-readable segment description")
    avg_blur: float = 0.0
    avg_brightness: float = 0.0
    avg_motion: float = 0.0
    has_audio: bool = False


class VideoQualityReport(BaseModel):
    """Complete quality analysis report for a video asset."""

    asset_id: str = Field(description="Asset identifier (UUID in FlightDeck)")
    source_file: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    codec: str = ""
    avg_blur: float = 0.0
    avg_brightness: float = 0.0
    avg_motion: float = 0.0
    dark_ratio: float = 0.0
    blurry_ratio: float = 0.0
    frame_analyses: list[FrameQualityMetrics] = Field(default_factory=list)
    scene_changes: list[SceneChange] = Field(default_factory=list)
    audio_analysis: AudioAnalysisResult = Field(default_factory=AudioAnalysisResult)


class DeliverableRequest(BaseModel):
    """Request to export a report-ready deliverable from a segment."""

    segment_id: str
    source_path: str
    start_time: float
    end_time: float
    duration: float
    has_audio: bool = False
    burn_timecode: bool = True
    burn_filename: bool = True
    target_width: int = 1920
    crf: int = 22
    source_label: str = ""

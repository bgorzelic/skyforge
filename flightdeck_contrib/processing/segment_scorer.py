"""Segment scoring and selection — intelligent segment extraction from quality reports.

Extracted from Skyforge's core/selector.py for FlightDeck integration.
Place in FlightDeck at: processing/src/segment_scorer.py

This module implements the scoring and selection algorithm that FlightDeck does
not currently have. It walks frame-level quality metrics, groups consecutive
good frames into candidate segments, splits at scene changes, and applies
duration constraints before tagging each segment.

Dependencies: flightdeck_contrib.schemas.quality
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from flightdeck_contrib.schemas.quality import (
    FrameQualityMetrics,
    SegmentQualityReport,
    VideoQualityReport,
)


class ScoredSegment(BaseModel):
    """A single selected segment with its quality report."""

    segment_id: int
    start_time: float
    end_time: float
    duration: float
    quality: SegmentQualityReport


class SelectionResult(BaseModel):
    """Selection results for a single video asset."""

    source_file: str
    total_duration: float
    segments: list[ScoredSegment] = Field(default_factory=list)
    selected_duration: float = 0.0
    rejected_duration: float = 0.0


class SegmentScorer:
    """Scores and selects usable video segments from a VideoQualityReport.

    Strategy:
    1. Walk frame analyses in time order.
    2. Score each frame on quality (sharpness, brightness, motion).
    3. Merge consecutive good frames into candidate segments.
    4. Split candidates at scene change boundaries.
    5. Apply min/max duration constraints, splitting long segments.
    6. Tag each segment with descriptive reason codes.

    Example usage in FlightDeck worker::

        scorer = SegmentScorer()
        result = scorer.select_segments(quality_report, min_confidence=0.4)
        for seg in result.segments:
            print(seg.segment_id, seg.quality.confidence, seg.quality.reason_tags)
    """

    def select_segments(
        self,
        quality_report: VideoQualityReport,
        min_segment: float = 5.0,
        max_segment: float = 25.0,
        min_confidence: float = 0.3,
        blur_threshold: float = 80.0,
        dark_threshold: float = 40.0,
    ) -> SelectionResult:
        """Select usable segments from a VideoQualityReport.

        Args:
            quality_report: Output of QualityAnalyzer.analyze_video().
            min_segment: Minimum segment duration in seconds.
            max_segment: Maximum segment duration in seconds before splitting.
            min_confidence: Minimum per-frame score to include a frame.
            blur_threshold: Laplacian variance below this is considered blurry.
            dark_threshold: Mean brightness below this is considered dark.

        Returns:
            SelectionResult containing all selected ScoredSegments.
        """
        result = SelectionResult(
            source_file=quality_report.source_file,
            total_duration=quality_report.duration,
        )

        if not quality_report.frame_analyses:
            return result

        # Build scene change timestamps for splitting
        scene_times = {
            round(sc.timestamp, 1) for sc in quality_report.scene_changes
        }

        # Score each frame
        scored_frames: list[tuple[FrameQualityMetrics, float, list[str]]] = []
        for fa in quality_report.frame_analyses:
            score, tags = _score_frame(fa, blur_threshold, dark_threshold)
            scored_frames.append((fa, score, tags))

        # Group consecutive good frames into candidate segments
        candidates: list[tuple[float, list[tuple[FrameQualityMetrics, float, list[str]]]]] = []
        current_start: float | None = None
        current_frames: list[tuple[FrameQualityMetrics, float, list[str]]] = []

        for fa, score, tags in scored_frames:
            is_good = score >= min_confidence
            at_scene_change = round(fa.timestamp, 1) in scene_times

            if is_good and not at_scene_change:
                if current_start is None:
                    current_start = fa.timestamp
                current_frames.append((fa, score, tags))
            else:
                if current_frames and current_start is not None:
                    candidates.append((current_start, current_frames))
                current_start = None
                current_frames = []

                # Good frame at scene change starts a new segment
                if is_good:
                    current_start = fa.timestamp
                    current_frames = [(fa, score, tags)]

        if current_frames and current_start is not None:
            candidates.append((current_start, current_frames))

        # Convert candidates to ScoredSegments with duration constraints
        seg_id = 1
        for start, frames in candidates:
            end = frames[-1][0].timestamp + 1.0  # extend ~1s past last sample
            end = min(end, quality_report.duration)
            duration = end - start

            if duration < min_segment:
                result.rejected_duration += duration
                continue

            # Split long segments at max_segment boundaries
            seg_start = start
            while seg_start < end:
                seg_end = min(seg_start + max_segment, end)
                seg_dur = seg_end - seg_start

                if seg_dur < min_segment and seg_start > start:
                    # Remainder too short — discard tail
                    break

                seg_frames = [
                    f for f, s, t in frames if seg_start <= f.timestamp < seg_end
                ]
                if not seg_frames:
                    seg_start = seg_end
                    continue

                avg_blur = sum(f.blur_score for f in seg_frames) / len(seg_frames)
                avg_brightness = sum(f.brightness for f in seg_frames) / len(seg_frames)
                avg_motion = sum(f.motion_score for f in seg_frames) / len(seg_frames)
                avg_score = (
                    sum(s for f, s, t in frames if seg_start <= f.timestamp < seg_end)
                    / len(seg_frames)
                )

                reason_tags = self.tag_segment(
                    seg_frames,
                    avg_motion=avg_motion,
                    avg_blur=avg_blur,
                    avg_brightness=avg_brightness,
                    width=quality_report.width,
                    height=quality_report.height,
                    has_audio=quality_report.has_audio,
                )

                confidence = round(min(avg_score, 1.0), 3)
                notes = self.generate_notes(confidence, reason_tags)

                quality = SegmentQualityReport(
                    confidence=confidence,
                    reason_tags=reason_tags,
                    notes=notes,
                    avg_blur=round(avg_blur, 2),
                    avg_brightness=round(avg_brightness, 2),
                    avg_motion=round(avg_motion, 2),
                    has_audio=quality_report.has_audio,
                )

                result.segments.append(
                    ScoredSegment(
                        segment_id=seg_id,
                        start_time=round(seg_start, 2),
                        end_time=round(seg_end, 2),
                        duration=round(seg_dur, 2),
                        quality=quality,
                    )
                )
                seg_id += 1
                seg_start = seg_end

        result.selected_duration = round(sum(s.duration for s in result.segments), 2)
        result.rejected_duration = round(
            result.total_duration - result.selected_duration, 2
        )
        return result

    @staticmethod
    def tag_segment(
        frames: list[FrameQualityMetrics],
        avg_motion: float,
        avg_blur: float,
        avg_brightness: float,
        width: int,
        height: int,
        has_audio: bool,
    ) -> list[str]:
        """Generate descriptive tags for a segment based on aggregate statistics.

        Tags are used downstream by FlightDeck's UI and search/filter features.

        Args:
            frames: Frame metrics belonging to this segment.
            avg_motion: Mean motion score across the segment.
            avg_blur: Mean blur score across the segment.
            avg_brightness: Mean brightness across the segment.
            width: Video width in pixels.
            height: Video height in pixels.
            has_audio: Whether the source asset has an audio track.

        Returns:
            List of string tags (e.g. "establishing_shot", "slow_pan", "4k").
        """
        tags: list[str] = []

        # Motion-based shot classification
        if avg_motion < 1.0:
            tags.append("static_shot")
        elif avg_motion < 5.0:
            tags.append("slow_pan")
        elif avg_motion < 15.0:
            tags.append("moderate_motion")
        else:
            tags.append("fast_motion")

        # Sharpness tags
        if avg_blur > 200:
            tags.append("very_sharp")
        elif avg_blur > 100:
            tags.append("clear")

        # Exposure tags
        if 80 < avg_brightness < 180:
            tags.append("good_exposure")

        # Resolution/orientation
        if width >= 3840:
            tags.append("4k")
        if height > width:
            tags.append("portrait")
        if not has_audio:
            tags.append("no_audio")

        # Shot type heuristics from motion patterns
        if len(frames) > 10:
            motion_trend = [f.motion_score for f in frames]
            if all(m < 2.0 for m in motion_trend[:3]) and avg_motion > 3.0:
                tags.append("reveal_shot")
            if all(m < 1.5 for m in motion_trend):
                tags.append("establishing_shot")

        return tags

    @staticmethod
    def generate_notes(confidence: float, tags: list[str]) -> str:
        """Generate a human-readable description for a segment.

        Args:
            confidence: Composite quality confidence 0.0-1.0.
            tags: Reason tags from tag_segment().

        Returns:
            A single descriptive sentence.
        """
        parts: list[str] = []

        if confidence > 0.8:
            parts.append("High quality segment")
        elif confidence > 0.5:
            parts.append("Usable segment")
        else:
            parts.append("Marginal segment")

        if "establishing_shot" in tags:
            parts.append("— potential establishing shot")
        if "fast_motion" in tags:
            parts.append("— action/movement")
        if "no_audio" in tags:
            parts.append("(no audio)")

        return ". ".join(parts)


# ============================================================================
# Internal frame scoring (mirrors selector.py:197-246 exactly)
# ============================================================================


def _score_frame(
    fa: FrameQualityMetrics,
    blur_threshold: float,
    dark_threshold: float,
) -> tuple[float, list[str]]:
    """Score a single frame 0-1 and return reason tags.

    This is the canonical scoring implementation. The same logic exists on
    QualityAnalyzer.score_frame() — both must stay in sync when tuning weights.
    """
    score = 1.0
    tags: list[str] = []

    if fa.is_blurry:
        score -= 0.5
        tags.append("blurry")
    elif fa.blur_score > blur_threshold * 3:
        tags.append("sharp")

    if fa.is_dark:
        score -= 0.6
        tags.append("too_dark")
    elif fa.brightness < 60:
        score -= 0.2
        tags.append("dim")

    if fa.is_overexposed:
        score -= 0.4
        tags.append("overexposed")

    if fa.contrast < 15:
        score -= 0.5
        tags.append("low_contrast")

    if 2.0 < fa.motion_score < 20.0:
        score += 0.1
        tags.append("good_motion")
    elif fa.motion_score < 0.5:
        tags.append("static")
    elif fa.motion_score > 30.0:
        score -= 0.2
        tags.append("shaky")

    if 80 < fa.brightness < 180 and fa.contrast > 30:
        score += 0.1
        tags.append("well_exposed")

    return max(0.0, min(1.0, score)), tags

"""Segment selector — score and select usable video segments from analysis data."""

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

from skyforge.core.analyzer import VideoAnalysis, FrameAnalysis


@dataclass
class Segment:
    """A selected video segment with quality scoring."""
    source_file: str
    segment_id: int
    start_time: float
    end_time: float
    duration: float
    confidence: float  # 0-1
    reason_tags: list[str] = field(default_factory=list)
    notes: str = ""
    avg_blur: float = 0.0
    avg_brightness: float = 0.0
    avg_motion: float = 0.0
    has_audio: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SelectsResult:
    """Selection results for a single video."""
    source_file: str
    total_duration: float
    segments: list[Segment] = field(default_factory=list)
    rejected_duration: float = 0.0
    selected_duration: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def select_segments(
    analysis: VideoAnalysis,
    min_segment: float = 5.0,
    max_segment: float = 25.0,
    blur_threshold: float = 80.0,
    dark_threshold: float = 40.0,
    min_confidence: float = 0.3,
) -> SelectsResult:
    """Select usable segments from a video analysis.

    Strategy:
    1. Walk through frame analyses in time order
    2. Score each frame on quality (sharpness, brightness, motion)
    3. Merge consecutive good frames into segments
    4. Split at scene changes
    5. Filter by minimum confidence and duration
    6. Tag each segment with reason codes
    """
    result = SelectsResult(
        source_file=analysis.source_file,
        total_duration=analysis.duration,
    )

    if not analysis.frame_analyses:
        return result

    # Build scene change timestamps for splitting
    scene_times = {round(sc.timestamp, 1) for sc in analysis.scene_changes}

    # Score each frame
    scored_frames = []
    for fa in analysis.frame_analyses:
        score, tags = _score_frame(fa, blur_threshold, dark_threshold)
        scored_frames.append((fa, score, tags))

    # Group consecutive good frames into candidate segments
    candidates = []
    current_start = None
    current_frames = []

    for fa, score, tags in scored_frames:
        is_good = score >= min_confidence
        at_scene_change = round(fa.timestamp, 1) in scene_times

        if is_good and not at_scene_change:
            if current_start is None:
                current_start = fa.timestamp
            current_frames.append((fa, score, tags))
        else:
            # Flush current segment
            if current_frames:
                candidates.append((current_start, current_frames))
            current_start = None
            current_frames = []

            # If this frame is good but at a scene change, start a new segment
            if is_good:
                current_start = fa.timestamp
                current_frames = [(fa, score, tags)]

    # Flush final segment
    if current_frames:
        candidates.append((current_start, current_frames))

    # Convert candidates to Segments, applying duration constraints
    seg_id = 1
    for start, frames in candidates:
        end = frames[-1][0].timestamp + 1.0  # extend by ~1s past last sample
        end = min(end, analysis.duration)
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
                # Too short after splitting — merge with previous if possible
                break

            # Compute segment-level stats
            seg_frames = [f for f, s, t in frames if seg_start <= f.timestamp < seg_end]
            if not seg_frames:
                seg_start = seg_end
                continue

            avg_blur = sum(f.blur_score for f in seg_frames) / len(seg_frames)
            avg_brightness = sum(f.brightness for f in seg_frames) / len(seg_frames)
            avg_motion = sum(f.motion_score for f in seg_frames) / len(seg_frames)
            avg_score = sum(s for f, s, t in frames if seg_start <= f.timestamp < seg_end) / len(seg_frames)

            # Determine tags
            tags = _tag_segment(seg_frames, avg_motion, avg_blur, avg_brightness, analysis)

            segment = Segment(
                source_file=analysis.source_file,
                segment_id=seg_id,
                start_time=round(seg_start, 2),
                end_time=round(seg_end, 2),
                duration=round(seg_dur, 2),
                confidence=round(min(avg_score, 1.0), 3),
                reason_tags=tags,
                avg_blur=round(avg_blur, 2),
                avg_brightness=round(avg_brightness, 2),
                avg_motion=round(avg_motion, 2),
                has_audio=analysis.has_audio,
            )
            segment.notes = _generate_notes(segment)

            result.segments.append(segment)
            seg_id += 1
            seg_start = seg_end

    result.selected_duration = round(sum(s.duration for s in result.segments), 2)
    result.rejected_duration = round(result.total_duration - result.selected_duration, 2)

    return result


def generate_master_timeline(selects_list: list[SelectsResult], output: Path) -> None:
    """Combine all per-video selects into a master timeline."""
    all_segments = []
    for sel in selects_list:
        for seg in sel.segments:
            all_segments.append(seg.to_dict())

    # Sort by confidence descending
    all_segments.sort(key=lambda x: x["confidence"], reverse=True)

    master = {
        "total_sources": len(selects_list),
        "total_segments": len(all_segments),
        "total_selected_duration": round(sum(s["duration"] for s in all_segments), 2),
        "segments": all_segments,
    }

    output.write_text(json.dumps(master, indent=2))


def save_selects(selects: SelectsResult, output: Path) -> None:
    """Save per-video selects to JSON."""
    output.write_text(json.dumps(selects.to_dict(), indent=2))


# ============================================================================
# Internal scoring
# ============================================================================

def _score_frame(
    fa: FrameAnalysis,
    blur_threshold: float,
    dark_threshold: float,
) -> tuple[float, list[str]]:
    """Score a single frame 0-1 and return reason tags."""
    score = 1.0
    tags = []

    # Penalize blur
    if fa.is_blurry:
        score -= 0.5
        tags.append("blurry")
    elif fa.blur_score > blur_threshold * 3:
        tags.append("sharp")

    # Penalize darkness
    if fa.is_dark:
        score -= 0.6
        tags.append("too_dark")
    elif fa.brightness < 60:
        score -= 0.2
        tags.append("dim")

    # Penalize overexposure
    if fa.is_overexposed:
        score -= 0.4
        tags.append("overexposed")

    # Low contrast (lens covered, fog, etc.)
    if fa.contrast < 15:
        score -= 0.5
        tags.append("low_contrast")

    # Reward moderate motion (interesting content)
    if 2.0 < fa.motion_score < 20.0:
        score += 0.1
        tags.append("good_motion")
    elif fa.motion_score < 0.5:
        tags.append("static")
    elif fa.motion_score > 30.0:
        score -= 0.2
        tags.append("shaky")

    # Reward good exposure
    if 80 < fa.brightness < 180 and fa.contrast > 30:
        score += 0.1
        tags.append("well_exposed")

    return max(0.0, min(1.0, score)), tags


def _tag_segment(
    frames: list[FrameAnalysis],
    avg_motion: float,
    avg_blur: float,
    avg_brightness: float,
    analysis: VideoAnalysis,
) -> list[str]:
    """Generate descriptive tags for a segment."""
    tags = []

    # Motion-based classification
    if avg_motion < 1.0:
        tags.append("static_shot")
    elif avg_motion < 5.0:
        tags.append("slow_pan")
    elif avg_motion < 15.0:
        tags.append("moderate_motion")
    else:
        tags.append("fast_motion")

    # Quality tags
    if avg_blur > 200:
        tags.append("very_sharp")
    elif avg_blur > 100:
        tags.append("clear")

    if 80 < avg_brightness < 180:
        tags.append("good_exposure")

    # Content type heuristics
    if analysis.width >= 3840:
        tags.append("4k")
    if analysis.height > analysis.width:
        tags.append("portrait")
    if not analysis.has_audio:
        tags.append("no_audio")

    # Shot type guesses based on motion patterns
    if len(frames) > 10:
        motion_trend = [f.motion_score for f in frames]
        if all(m < 2.0 for m in motion_trend[:3]) and avg_motion > 3.0:
            tags.append("reveal_shot")
        if all(m < 1.5 for m in motion_trend):
            tags.append("establishing_shot")

    return tags


def _generate_notes(segment: Segment) -> str:
    """Generate human-readable notes for a segment."""
    parts = []
    if segment.confidence > 0.8:
        parts.append("High quality segment")
    elif segment.confidence > 0.5:
        parts.append("Usable segment")
    else:
        parts.append("Marginal segment")

    if "establishing_shot" in segment.reason_tags:
        parts.append("— potential establishing shot")
    if "fast_motion" in segment.reason_tags:
        parts.append("— action/movement")
    if "no_audio" in segment.reason_tags:
        parts.append("(no audio)")

    return ". ".join(parts)

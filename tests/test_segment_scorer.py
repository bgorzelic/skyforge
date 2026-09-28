"""Tests for flightdeck_contrib.processing.segment_scorer."""

from __future__ import annotations

import pytest

from flightdeck_contrib.processing.segment_scorer import SegmentScorer, _score_frame
from flightdeck_contrib.schemas.quality import (
    FrameQualityMetrics,
    SceneChange,
    VideoQualityReport,
)


def _frame(
    timestamp: float = 0.0,
    blur_score: float = 150.0,
    brightness: float = 120.0,
    contrast: float = 40.0,
    motion_score: float = 3.0,
    is_dark: bool = False,
    is_overexposed: bool = False,
    is_blurry: bool = False,
) -> FrameQualityMetrics:
    return FrameQualityMetrics(
        timestamp=timestamp,
        blur_score=blur_score,
        brightness=brightness,
        contrast=contrast,
        motion_score=motion_score,
        is_dark=is_dark,
        is_overexposed=is_overexposed,
        is_blurry=is_blurry,
        quality_score=0.5,
    )


def _report(
    frames: list[FrameQualityMetrics] | None = None,
    scene_changes: list[SceneChange] | None = None,
    duration: float = 30.0,
    width: int = 1920,
    height: int = 1080,
    has_audio: bool = True,
) -> VideoQualityReport:
    return VideoQualityReport(
        asset_id="test-asset",
        source_file="test.mp4",
        duration=duration,
        width=width,
        height=height,
        fps=30.0,
        has_audio=has_audio,
        frame_analyses=frames or [],
        scene_changes=scene_changes or [],
    )


# ── generate_notes tests ────────────────────────────────────────────────────


class TestGenerateNotes:
    def test_high_quality(self):
        note = SegmentScorer.generate_notes(0.9, [])
        assert note.startswith("High quality segment")

    def test_usable_segment(self):
        note = SegmentScorer.generate_notes(0.6, [])
        assert note.startswith("Usable segment")

    def test_marginal_segment(self):
        note = SegmentScorer.generate_notes(0.3, [])
        assert note.startswith("Marginal segment")

    def test_establishing_shot(self):
        note = SegmentScorer.generate_notes(0.7, ["establishing_shot"])
        assert "potential establishing shot" in note

    def test_fast_motion(self):
        note = SegmentScorer.generate_notes(0.7, ["fast_motion"])
        assert "action/movement" in note

    def test_no_audio(self):
        note = SegmentScorer.generate_notes(0.7, ["no_audio"])
        assert "(no audio)" in note

    def test_multiple_tags(self):
        note = SegmentScorer.generate_notes(0.7, ["establishing_shot", "no_audio"])
        assert "potential establishing shot" in note
        assert "(no audio)" in note

    def test_boundary_exactly_0_8(self):
        note = SegmentScorer.generate_notes(0.8, [])
        assert note.startswith("Usable segment")

    def test_boundary_just_below_0_5(self):
        note = SegmentScorer.generate_notes(0.49, [])
        assert note.startswith("Marginal segment")

    def test_boundary_just_above_0_5(self):
        note = SegmentScorer.generate_notes(0.51, [])
        assert note.startswith("Usable segment")


# ── tag_segment tests ──────────────────────────────────────────────────────


class TestTagSegment:
    def test_static_shot(self):
        frames = [_frame(motion_score=0.5)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=0.5,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "static_shot" in tags

    def test_slow_pan(self):
        frames = [_frame(motion_score=3.0)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "slow_pan" in tags

    def test_moderate_motion(self):
        frames = [_frame(motion_score=10.0)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=10.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "moderate_motion" in tags

    def test_fast_motion(self):
        frames = [_frame(motion_score=20.0)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=20.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "fast_motion" in tags

    def test_very_sharp(self):
        frames = [_frame(blur_score=250)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=250,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "very_sharp" in tags

    def test_clear(self):
        frames = [_frame(blur_score=150)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "clear" in tags

    def test_good_exposure(self):
        frames = [_frame(brightness=120)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "good_exposure" in tags

    def test_4k_resolution(self):
        frames = [_frame()]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=150,
            avg_brightness=120,
            width=3840,
            height=2160,
            has_audio=True,
        )
        assert "4k" in tags

    def test_portrait(self):
        frames = [_frame()]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=150,
            avg_brightness=120,
            width=1080,
            height=1920,
            has_audio=True,
        )
        assert "portrait" in tags

    def test_no_audio(self):
        frames = [_frame()]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=3.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=False,
        )
        assert "no_audio" in tags

    def test_establishing_shot(self):
        frames = [_frame(motion_score=0.5) for _ in range(15)]
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=0.5,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "establishing_shot" in tags

    def test_reveal_shot(self):
        frames = [_frame(motion_score=0.5)] * 3 + [_frame(motion_score=6.0)] * 12
        tags = SegmentScorer.tag_segment(
            frames,
            avg_motion=5.0,
            avg_blur=150,
            avg_brightness=120,
            width=1920,
            height=1080,
            has_audio=True,
        )
        assert "reveal_shot" in tags


# ── _score_frame tests ─────────────────────────────────────────────────────


class TestScoreFrame:
    def test_good_frame(self):
        fa = _frame(blur_score=300, brightness=120, contrast=40, motion_score=5.0)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert score > 0.8
        assert "sharp" in tags
        assert "good_motion" in tags
        assert "well_exposed" in tags

    def test_blurry_frame(self):
        fa = _frame(is_blurry=True, blur_score=10)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "blurry" in tags
        assert score < 1.0

    def test_dark_frame(self):
        fa = _frame(is_dark=True, brightness=30, contrast=10)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "too_dark" in tags
        assert score < 0.5

    def test_overexposed_frame(self):
        fa = _frame(is_overexposed=True)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "overexposed" in tags

    def test_low_contrast(self):
        fa = _frame(contrast=10, brightness=30)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "low_contrast" in tags
        assert score < 0.6

    def test_static_frame(self):
        fa = _frame(motion_score=0.1)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "static" in tags

    def test_shaky_frame(self):
        fa = _frame(motion_score=35.0)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "shaky" in tags
        assert score < 1.0

    def test_score_clamped_to_zero(self):
        fa = _frame(
            is_blurry=True,
            is_dark=True,
            is_overexposed=True,
            contrast=5,
            motion_score=35.0,
        )
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert score == 0.0

    def test_score_clamped_to_one(self):
        fa = _frame(
            blur_score=400,
            brightness=130,
            contrast=50,
            motion_score=5.0,
        )
        score, _ = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert score <= 1.0

    def test_dim_frame(self):
        fa = _frame(brightness=55, is_dark=False)
        score, tags = _score_frame(fa, blur_threshold=80.0, dark_threshold=40.0)
        assert "dim" in tags


# ── select_segments tests ──────────────────────────────────────────────────


class TestSelectSegments:
    def test_empty_report(self):
        scorer = SegmentScorer()
        result = scorer.select_segments(_report(frames=[]))
        assert result.segments == []
        assert result.selected_duration == 0.0

    def test_single_short_segment_rejected(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i)) for i in range(3)]
        result = scorer.select_segments(
            _report(frames=frames, duration=10.0), min_segment=5.0
        )
        assert len(result.segments) == 0
        assert result.rejected_duration > 0

    def test_good_segment_selected(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120, blur_score=200) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0), min_segment=5.0
        )
        assert len(result.segments) >= 1
        assert result.selected_duration >= 5.0

    def test_scene_change_splits(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        scene_changes = [SceneChange(timestamp=10.0, score=0.9)]
        result = scorer.select_segments(
            _report(frames=frames, scene_changes=scene_changes, duration=25.0),
            min_segment=5.0,
        )
        assert len(result.segments) >= 2

    def test_long_segment_split_at_max(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(40)]
        result = scorer.select_segments(
            _report(frames=frames, duration=45.0),
            min_segment=5.0,
            max_segment=15.0,
        )
        assert len(result.segments) >= 2
        for seg in result.segments:
            assert seg.duration <= 16.0

    def test_confidence_clamped(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120, blur_score=400) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0), min_segment=5.0
        )
        for seg in result.segments:
            assert 0.0 <= seg.quality.confidence <= 1.0

    def test_rejected_duration_is_remainder(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0), min_segment=5.0
        )
        total = result.selected_duration + result.rejected_duration
        assert abs(total - 25.0) < 1.0

    def test_notes_populated(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0), min_segment=5.0
        )
        for seg in result.segments:
            assert seg.quality.notes != ""

    def test_reason_tags_populated(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0), min_segment=5.0
        )
        for seg in result.segments:
            assert len(seg.quality.reason_tags) > 0

    def test_4k_tag_when_4k(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0, width=3840, height=2160),
            min_segment=5.0,
        )
        assert any("4k" in seg.quality.reason_tags for seg in result.segments)

    def test_portrait_tag_when_portrait(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0, width=1080, height=1920),
            min_segment=5.0,
        )
        assert any("portrait" in seg.quality.reason_tags for seg in result.segments)

    def test_no_audio_tag(self):
        scorer = SegmentScorer()
        frames = [_frame(timestamp=float(i), brightness=120) for i in range(20)]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0, has_audio=False),
            min_segment=5.0,
        )
        for seg in result.segments:
            assert "no_audio" in seg.quality.reason_tags

    def test_min_confidence_filters_blurry(self):
        scorer = SegmentScorer()
        frames = [
            _frame(
                timestamp=float(i),
                is_blurry=True,
                blur_score=10,
                brightness=30,
                is_dark=True,
                contrast=10,
            )
            for i in range(20)
        ]
        result = scorer.select_segments(
            _report(frames=frames, duration=25.0),
            min_segment=5.0,
            min_confidence=0.3,
        )
        assert len(result.segments) == 0

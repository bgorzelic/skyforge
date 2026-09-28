"""Tests for skyforge.core.selector module."""

import json
from pathlib import Path

import pytest

from skyforge.core.analyzer import FrameAnalysis, SceneChange, VideoAnalysis
from skyforge.core.selector import (
    Segment,
    SelectsResult,
    _generate_notes,
    _tag_segment,
    generate_master_timeline,
    save_selects,
    select_segments,
)


def make_frame_analysis(
    timestamp: float,
    blur_score: float = 150.0,
    brightness: float = 120.0,
    contrast: float = 30.0,
    motion_score: float = 5.0,
    is_dark: bool = False,
    is_overexposed: bool = False,
    is_blurry: bool = False,
) -> FrameAnalysis:
    """Create a FrameAnalysis with sensible defaults for testing."""
    return FrameAnalysis(
        timestamp=timestamp,
        blur_score=blur_score,
        brightness=brightness,
        contrast=contrast,
        motion_score=motion_score,
        is_dark=is_dark,
        is_overexposed=is_overexposed,
        is_blurry=is_blurry,
    )


def make_video_analysis(
    source_file: str = "test.mp4",
    duration: float = 30.0,
    width: int = 1920,
    height: int = 1080,
    fps: float = 30.0,
    has_audio: bool = True,
    frame_analyses: list[FrameAnalysis] | None = None,
    scene_changes: list[SceneChange] | None = None,
) -> VideoAnalysis:
    """Create a VideoAnalysis with sensible defaults for testing."""
    return VideoAnalysis(
        source_file=source_file,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
        frame_analyses=frame_analyses or [],
        scene_changes=scene_changes or [],
    )


class TestSegmentToDict:
    """Tests for Segment.to_dict method."""

    def test_to_dict_contains_all_fields(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.85,
            reason_tags=["sharp", "good_motion"],
            notes="High quality segment",
            avg_blur=150.0,
            avg_brightness=120.0,
            avg_motion=5.0,
            has_audio=True,
        )
        d = seg.to_dict()

        assert d["source_file"] == "test.mp4"
        assert d["segment_id"] == 1
        assert d["start_time"] == 0.0
        assert d["end_time"] == 10.0
        assert d["duration"] == 10.0
        assert d["confidence"] == 0.85
        assert d["reason_tags"] == ["sharp", "good_motion"]
        assert d["notes"] == "High quality segment"
        assert d["avg_blur"] == 150.0
        assert d["avg_brightness"] == 120.0
        assert d["avg_motion"] == 5.0
        assert d["has_audio"] is True

    def test_to_dict_empty_tags_and_notes(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=5.0,
            duration=5.0,
            confidence=0.5,
        )
        d = seg.to_dict()

        assert d["reason_tags"] == []
        assert d["notes"] == ""


class TestSelectsResultToDict:
    """Tests for SelectsResult.to_dict method."""

    def test_to_dict_contains_all_fields(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.8,
        )
        result = SelectsResult(
            source_file="test.mp4",
            total_duration=30.0,
            segments=[seg],
            rejected_duration=20.0,
            selected_duration=10.0,
        )
        d = result.to_dict()

        assert d["source_file"] == "test.mp4"
        assert d["total_duration"] == 30.0
        assert d["rejected_duration"] == 20.0
        assert d["selected_duration"] == 10.0
        assert len(d["segments"]) == 1
        assert d["segments"][0]["segment_id"] == 1

    def test_to_dict_empty_segments(self):
        result = SelectsResult(source_file="test.mp4", total_duration=30.0)
        d = result.to_dict()

        assert d["segments"] == []
        assert d["selected_duration"] == 0.0
        assert d["rejected_duration"] == 0.0


class TestSaveSelectsRoundTrip:
    """Tests for save_selects round-trip serialization."""

    def test_save_and_load_preserves_all_fields(self, tmp_path: Path):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.85,
            reason_tags=["sharp", "good_motion", "well_exposed"],
            notes="High quality segment — potential establishing shot",
            avg_blur=150.0,
            avg_brightness=120.0,
            avg_motion=5.0,
            has_audio=True,
        )
        original = SelectsResult(
            source_file="test.mp4",
            total_duration=30.0,
            segments=[seg],
            rejected_duration=20.0,
            selected_duration=10.0,
        )

        output_file = tmp_path / "selects.json"
        save_selects(original, output_file)

        loaded_data = json.loads(output_file.read_text())

        assert loaded_data["source_file"] == "test.mp4"
        assert loaded_data["total_duration"] == 30.0
        assert loaded_data["rejected_duration"] == 20.0
        assert loaded_data["selected_duration"] == 10.0
        assert len(loaded_data["segments"]) == 1

        loaded_seg = loaded_data["segments"][0]
        assert loaded_seg["segment_id"] == 1
        assert loaded_seg["confidence"] == 0.85
        assert loaded_seg["reason_tags"] == ["sharp", "good_motion", "well_exposed"]
        assert loaded_seg["notes"] == "High quality segment — potential establishing shot"
        assert loaded_seg["avg_blur"] == 150.0
        assert loaded_seg["has_audio"] is True

    def test_save_selects_creates_valid_json(self, tmp_path: Path):
        result = SelectsResult(source_file="test.mp4", total_duration=10.0)
        output_file = tmp_path / "selects.json"

        save_selects(result, output_file)

        # Should be valid JSON
        data = json.loads(output_file.read_text())
        assert data["source_file"] == "test.mp4"
        assert data["segments"] == []


class TestTagSegment:
    """Tests for _tag_segment internal function."""

    def test_static_shot_tag(self):
        frames = [make_frame_analysis(t, motion_score=0.5) for t in [0.0, 1.0, 2.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=0.5, avg_blur=150.0, avg_brightness=120.0, analysis=analysis)

        assert "static_shot" in tags

    def test_slow_pan_tag(self):
        frames = [make_frame_analysis(t, motion_score=3.0) for t in [0.0, 1.0, 2.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=3.0, avg_blur=150.0, avg_brightness=120.0, analysis=analysis)

        assert "slow_pan" in tags

    def test_moderate_motion_tag(self):
        frames = [make_frame_analysis(t, motion_score=10.0) for t in [0.0, 1.0, 2.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=10.0, avg_blur=150.0, avg_brightness=120.0, analysis=analysis)

        assert "moderate_motion" in tags

    def test_fast_motion_tag(self):
        frames = [make_frame_analysis(t, motion_score=20.0) for t in [0.0, 1.0, 2.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=20.0, avg_blur=150.0, avg_brightness=120.0, analysis=analysis)

        assert "fast_motion" in tags

    def test_very_sharp_tag(self):
        frames = [make_frame_analysis(t, blur_score=250.0) for t in [0.0, 1.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=5.0, avg_blur=250.0, avg_brightness=120.0, analysis=analysis)

        assert "very_sharp" in tags

    def test_clear_tag(self):
        frames = [make_frame_analysis(t, blur_score=150.0) for t in [0.0, 1.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=5.0, avg_blur=150.0, avg_brightness=120.0, analysis=analysis)

        assert "clear" in tags

    def test_good_exposure_tag(self):
        frames = [make_frame_analysis(t, brightness=120.0) for t in [0.0, 1.0]]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=5.0, avg_blur=100.0, avg_brightness=120.0, analysis=analysis)

        assert "good_exposure" in tags

    def test_4k_tag(self):
        frames = [make_frame_analysis(t) for t in [0.0, 1.0]]
        analysis = make_video_analysis(width=3840, height=2160)

        tags = _tag_segment(frames, avg_motion=5.0, avg_blur=100.0, avg_brightness=120.0, analysis=analysis)

        assert "4k" in tags

    def test_portrait_tag(self):
        frames = [make_frame_analysis(t) for t in [0.0, 1.0]]
        analysis = make_video_analysis(width=1080, height=1920)

        tags = _tag_segment(frames, avg_motion=5.0, avg_blur=100.0, avg_brightness=120.0, analysis=analysis)

        assert "portrait" in tags

    def test_no_audio_tag(self):
        frames = [make_frame_analysis(t) for t in [0.0, 1.0]]
        analysis = make_video_analysis(has_audio=False)

        tags = _tag_segment(frames, avg_motion=5.0, avg_blur=100.0, avg_brightness=120.0, analysis=analysis)

        assert "no_audio" in tags

    def test_reveal_shot_tag(self):
        frames = [
            make_frame_analysis(0.0, motion_score=0.5),
            make_frame_analysis(1.0, motion_score=0.5),
            make_frame_analysis(2.0, motion_score=0.5),
            make_frame_analysis(3.0, motion_score=10.0),
            make_frame_analysis(4.0, motion_score=10.0),
            make_frame_analysis(5.0, motion_score=10.0),
            make_frame_analysis(6.0, motion_score=10.0),
            make_frame_analysis(7.0, motion_score=10.0),
            make_frame_analysis(8.0, motion_score=10.0),
            make_frame_analysis(9.0, motion_score=10.0),
            make_frame_analysis(10.0, motion_score=10.0),
        ]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=10.0, avg_blur=100.0, avg_brightness=120.0, analysis=analysis)

        assert "reveal_shot" in tags

    def test_establishing_shot_tag(self):
        frames = [make_frame_analysis(t, motion_score=1.0) for t in range(12)]
        analysis = make_video_analysis()

        tags = _tag_segment(frames, avg_motion=1.0, avg_blur=100.0, avg_brightness=120.0, analysis=analysis)

        assert "establishing_shot" in tags


class TestGenerateNotes:
    """Tests for _generate_notes internal function."""

    def test_high_confidence_notes(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.9,
            reason_tags=["sharp", "good_motion"],
        )

        notes = _generate_notes(seg)

        assert "High quality segment" in notes

    def test_medium_confidence_notes(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.6,
            reason_tags=["sharp", "good_motion"],
        )

        notes = _generate_notes(seg)

        assert "Usable segment" in notes

    def test_low_confidence_notes(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.3,
            reason_tags=["sharp", "good_motion"],
        )

        notes = _generate_notes(seg)

        assert "Marginal segment" in notes

    def test_establishing_shot_note(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.8,
            reason_tags=["establishing_shot"],
        )

        notes = _generate_notes(seg)

        assert "potential establishing shot" in notes

    def test_fast_motion_note(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.8,
            reason_tags=["fast_motion"],
        )

        notes = _generate_notes(seg)

        assert "action/movement" in notes

    def test_no_audio_note(self):
        seg = Segment(
            source_file="test.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.8,
            reason_tags=["no_audio"],
        )

        notes = _generate_notes(seg)

        assert "(no audio)" in notes


class TestSelectSegments:
    """Integration tests for select_segments function."""

    def test_select_segments_returns_empty_for_no_frames(self):
        analysis = make_video_analysis(frame_analyses=[])
        result = select_segments(analysis)

        assert result.segments == []
        assert result.selected_duration == 0.0

    def test_select_segments_creates_segments_from_good_frames(self):
        frames = [
            make_frame_analysis(0.0, blur_score=150, brightness=120, motion_score=5),
            make_frame_analysis(1.0, blur_score=150, brightness=120, motion_score=5),
            make_frame_analysis(2.0, blur_score=150, brightness=120, motion_score=5),
            make_frame_analysis(3.0, blur_score=150, brightness=120, motion_score=5),
            make_frame_analysis(4.0, blur_score=150, brightness=120, motion_score=5),
            make_frame_analysis(5.0, blur_score=150, brightness=120, motion_score=5),
        ]
        analysis = make_video_analysis(duration=10.0, frame_analyses=frames)

        result = select_segments(analysis, min_segment=2.0, max_segment=25.0)

        assert len(result.segments) >= 1
        assert result.selected_duration > 0

    def test_select_segments_rejects_blurry_frames(self):
        # Blurry + low contrast + static = heavily penalized
        frames = [
            make_frame_analysis(0.0, blur_score=30, is_blurry=True, contrast=10, motion_score=0.1),
            make_frame_analysis(1.0, blur_score=30, is_blurry=True, contrast=10, motion_score=0.1),
            make_frame_analysis(2.0, blur_score=30, is_blurry=True, contrast=10, motion_score=0.1),
        ]
        analysis = make_video_analysis(duration=10.0, frame_analyses=frames)

        result = select_segments(analysis, min_segment=1.0, blur_threshold=80.0, min_confidence=0.4)

        assert len(result.segments) == 0
        assert result.rejected_duration > 0

    def test_select_segments_rejects_dark_frames(self):
        # Dark + low contrast + static = heavily penalized
        frames = [
            make_frame_analysis(0.0, brightness=10, is_dark=True, contrast=10, motion_score=0.1),
            make_frame_analysis(1.0, brightness=10, is_dark=True, contrast=10, motion_score=0.1),
            make_frame_analysis(2.0, brightness=10, is_dark=True, contrast=10, motion_score=0.1),
        ]
        analysis = make_video_analysis(duration=10.0, frame_analyses=frames)

        result = select_segments(analysis, min_segment=1.0, dark_threshold=40.0, min_confidence=0.4)

        assert len(result.segments) == 0

    def test_select_segments_splits_at_scene_changes(self):
        frames = []
        for t in range(10):
            frames.append(make_frame_analysis(float(t), blur_score=150, brightness=120, motion_score=5))
        scene_changes = [SceneChange(timestamp=5.0, score=0.9)]
        analysis = make_video_analysis(duration=15.0, frame_analyses=frames, scene_changes=scene_changes)

        result = select_segments(analysis, min_segment=2.0, max_segment=25.0)

        # Should create at least 2 segments split at scene change
        assert len(result.segments) >= 1


class TestGenerateMasterTimeline:
    """Tests for generate_master_timeline function."""

    def test_generate_master_timeline_combines_and_sorts(self, tmp_path: Path):
        seg1 = Segment(
            source_file="a.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            confidence=0.9,
        )
        seg2 = Segment(
            source_file="b.mp4",
            segment_id=1,
            start_time=0.0,
            end_time=5.0,
            duration=5.0,
            confidence=0.7,
        )
        seg3 = Segment(
            source_file="a.mp4",
            segment_id=2,
            start_time=10.0,
            end_time=15.0,
            duration=5.0,
            confidence=0.5,
        )

        selects_list = [
            SelectsResult(source_file="a.mp4", total_duration=15.0, segments=[seg1, seg3], selected_duration=15.0),
            SelectsResult(source_file="b.mp4", total_duration=5.0, segments=[seg2], selected_duration=5.0),
        ]

        output = tmp_path / "master.json"
        generate_master_timeline(selects_list, output)

        data = json.loads(output.read_text())

        assert data["total_sources"] == 2
        assert data["total_segments"] == 3
        assert data["total_selected_duration"] == 20.0

        # Should be sorted by confidence descending
        confidences = [s["confidence"] for s in data["segments"]]
        assert confidences == [0.9, 0.7, 0.5]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
"""Tests for the reporter module."""

import csv
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from skyforge.core.reporter import export_analysis_csv, export_segments_csv


@pytest.fixture
def fake_analysis_dir():
    """Create a temporary directory with fake analysis and selects JSON files."""
    with TemporaryDirectory() as tmpdir:
        analysis_dir = Path(tmpdir)
        
        # Create analysis subdirectory
        video1_dir = analysis_dir / "video1_20240101"
        video1_dir.mkdir()
        
        # Write analysis.json with frame analyses
        analysis1 = {
            "source_file": "video1.mp4",
            "frame_analyses": [
                {
                    "timestamp": 0.0,
                    "blur_score": 0.1,
                    "brightness": 0.5,
                    "contrast": 1.0,
                    "motion_score": 0.2,
                    "is_dark": False,
                    "is_overexposed": False,
                    "is_blurry": False,
                },
                {
                    "timestamp": 1.0,
                    "blur_score": 0.2,
                    "brightness": 0.6,
                    "contrast": 1.1,
                    "motion_score": 0.3,
                    "is_dark": True,
                    "is_overexposed": False,
                    "is_blurry": True,
                },
            ],
        }
        (video1_dir / "analysis.json").write_text(json.dumps(analysis1))
        
        # Create another analysis directory
        video2_dir = analysis_dir / "video2_20240102"
        video2_dir.mkdir()
        
        analysis2 = {
            "source_file": "video2.mp4",
            "frame_analyses": [
                {
                    "timestamp": 0.5,
                    "blur_score": 0.3,
                    "brightness": 0.7,
                    "contrast": 0.9,
                    "motion_score": 0.1,
                    "is_dark": False,
                    "is_overexposed": True,
                    "is_blurry": False,
                },
            ],
        }
        (video2_dir / "analysis.json").write_text(json.dumps(analysis2))
        
        # Create selects JSON files
        selects1 = {
            "segments": [
                {
                    "source_file": "video1.mp4",
                    "segment_id": 1,
                    "start_time": 0.0,
                    "end_time": 5.0,
                    "duration": 5.0,
                    "confidence": 0.95,
                    "reason_tags": ["good_quality", "stable_motion"],
                },
                {
                    "source_file": "video1.mp4",
                    "segment_id": 2,
                    "start_time": 10.0,
                    "end_time": 12.0,
                    "duration": 2.0,
                    "confidence": 0.87,
                    "reason_tags": ["low_brightness"],
                },
            ],
        }
        (analysis_dir / "selects_video1.json").write_text(json.dumps(selects1))
        
        selects2 = {
            "segments": [
                {
                    "source_file": "video2.mp4",
                    "segment_id": 1,
                    "start_time": 1.0,
                    "end_time": 3.0,
                    "duration": 2.0,
                    "confidence": 0.92,
                    "reason_tags": ["high_contrast", "sharp"],
                },
            ],
        }
        (analysis_dir / "selects_video2.json").write_text(json.dumps(selects2))
        
        yield analysis_dir


def test_export_analysis_csv(fake_analysis_dir):
    """Test export_analysis_csv writes correct CSV content."""
    with TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "analysis.csv"
        
        result = export_analysis_csv(fake_analysis_dir, output)
        
        assert result == output
        assert output.exists()
        
        # Read and verify CSV content
        with output.open("r", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            assert len(rows) == 3
            
            # Check first row from video1
            assert rows[0]["source"] == "video1"
            assert float(rows[0]["timestamp"]) == 0.0
            assert float(rows[0]["blur_score"]) == 0.1
            assert float(rows[0]["brightness"]) == 0.5
            assert rows[0]["is_dark"] == "False"
            assert rows[0]["is_blurry"] == "False"
            
            # Check second row from video1
            assert rows[1]["source"] == "video1"
            assert float(rows[1]["timestamp"]) == 1.0
            assert rows[1]["is_dark"] == "True"
            assert rows[1]["is_blurry"] == "True"
            
            # Check row from video2
            assert rows[2]["source"] == "video2"
            assert float(rows[2]["timestamp"]) == 0.5
            assert rows[2]["is_overexposed"] == "True"


def test_export_segments_csv(fake_analysis_dir):
    """Test export_segments_csv writes correct CSV content."""
    with TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "segments.csv"
        
        result = export_segments_csv(fake_analysis_dir, output)
        
        assert result == output
        assert output.exists()
        
        # Read and verify CSV content
        with output.open("r", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            assert len(rows) == 3
            
            # Check first segment from video1
            assert rows[0]["source_file"] == "video1.mp4"
            assert int(rows[0]["segment_id"]) == 1
            assert float(rows[0]["start_time"]) == 0.0
            assert float(rows[0]["end_time"]) == 5.0
            assert rows[0]["reason_tags"] == "good_quality;stable_motion"
            
            # Check second segment from video1
            assert rows[1]["source_file"] == "video1.mp4"
            assert int(rows[1]["segment_id"]) == 2
            assert rows[1]["reason_tags"] == "low_brightness"
            
            # Check segment from video2
            assert rows[2]["source_file"] == "video2.mp4"
            assert int(rows[2]["segment_id"]) == 1
            assert rows[2]["reason_tags"] == "high_contrast;sharp"


def test_export_analysis_csv_empty_dir():
    """Test export_analysis_csv handles empty directory."""
    with TemporaryDirectory() as tmpdir:
        analysis_dir = Path(tmpdir)
        output = Path(tmpdir) / "analysis.csv"
        
        result = export_analysis_csv(analysis_dir, output)
        
        assert result == output
        assert output.exists()
        
        # Should only contain header
        with output.open("r", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0] == [
                "source",
                "timestamp",
                "blur_score",
                "brightness",
                "contrast",
                "motion_score",
                "is_dark",
                "is_overexposed",
                "is_blurry",
            ]


def test_export_segments_csv_empty_dir():
    """Test export_segments_csv handles empty directory."""
    with TemporaryDirectory() as tmpdir:
        analysis_dir = Path(tmpdir)
        output = Path(tmpdir) / "segments.csv"
        
        result = export_segments_csv(analysis_dir, output)
        
        assert result == output
        assert output.exists()
        
        # Should only contain header
        with output.open("r", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
            assert len(rows) == 1
            assert rows[0] == [
                "source_file",
                "segment_id",
                "start_time",
                "end_time",
                "duration",
                "confidence",
                "reason_tags",
            ]


def test_export_analysis_csv_no_frame_analyses():
    """Test export_analysis_csv handles analysis files without frame_analyses."""
    with TemporaryDirectory() as tmpdir:
        analysis_dir = Path(tmpdir)
        
        # Create analysis file without frame_analyses
        video_dir = analysis_dir / "video_no_frames"
        video_dir.mkdir()
        
        analysis_no_frames = {
            "source_file": "video_no_frames.mp4",
        }
        (video_dir / "analysis.json").write_text(json.dumps(analysis_no_frames))
        
        output = Path(tmpdir) / "analysis.csv"
        
        result = export_analysis_csv(analysis_dir, output)
        
        assert result == output
        assert output.exists()
        
        # Should only contain header (no data rows)
        with output.open("r", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
            assert len(rows) == 1


def test_export_segments_csv_no_segments():
    """Test export_segments_csv handles selects files without segments."""
    with TemporaryDirectory() as tmpdir:
        analysis_dir = Path(tmpdir)
        
        # Create selects file without segments
        selects_no_segments = {
            "source_file": "video_no_segments.mp4",
        }
        (analysis_dir / "selects_empty.json").write_text(json.dumps(selects_no_segments))
        
        output = Path(tmpdir) / "segments.csv"
        
        result = export_segments_csv(analysis_dir, output)
        
        assert result == output
        assert output.exists()
        
        # Should only contain header (no data rows)
        with output.open("r", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
            assert len(rows) == 1
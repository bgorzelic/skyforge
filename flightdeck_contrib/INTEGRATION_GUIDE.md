# FlightDeck Integration Guide

Extracted algorithms from Skyforge CLI, packaged for integration into FlightDeck's
processing service. This directory (`flightdeck_contrib/`) contains production-ready
modules that can be copied directly into FlightDeck's service structure.

## What's Included

### Schemas (`schemas/quality.py`)

Pydantic v2 models for quality analysis data:

| Model | Purpose |
|---|---|
| `FrameQualityMetrics` | Per-frame blur, brightness, contrast, motion scores |
| `SceneChange` | Scene boundary detection timestamp + score |
| `AudioPeak` / `SilenceRegion` | Audio analysis markers |
| `AudioAnalysisResult` | Complete audio analysis output |
| `SegmentQualityReport` | Segment confidence, tags, notes, avg metrics |
| `VideoQualityReport` | Full per-asset quality report with frames + scenes |
| `DeliverableRequest` | Export request with burn-in and format options |

**Target location:** `shared/src/schemas/quality.py`

### Quality Analyzer (`processing/quality_analyzer.py`)

Frame-level quality analysis using OpenCV:

- `QualityAnalyzer.analyze_frames()` - Sample frames at interval, compute blur (Laplacian variance), brightness, contrast, motion detection
- `QualityAnalyzer.detect_scene_changes()` - PySceneDetect ContentDetector with FFmpeg fallback
- `QualityAnalyzer.analyze_audio()` - FFmpeg silencedetect for audio analysis
- `QualityAnalyzer.extract_contact_sheet()` - Thumbnail montage via FFmpeg tile filter

All OpenCV operations are synchronous; wrap in `asyncio.to_thread()` for async workers.

**Target location:** `services/processing/src/quality_analyzer.py`

### Segment Scorer (`processing/segment_scorer.py`)

Intelligent segment scoring and selection:

- `SegmentScorer.score_frames()` - Per-frame quality scoring with blur/brightness/motion penalties
- `SegmentScorer.select_segments()` - Merge consecutive good frames, split at scene changes, enforce min/max duration
- `SegmentScorer.tag_segment()` - Classify shots (static_shot, slow_pan, reveal_shot, establishing_shot, fast_motion, etc.)
- `SegmentScorer.generate_notes()` - Human-readable segment descriptions

Scoring weights (from `ScorerConfig`):
- Blur penalty: -0.5
- Dark frame: -0.6 / dim: -0.2
- Overexposure: -0.4
- Low contrast: -0.5
- Good motion bonus: +0.1
- Excessive motion: -0.2
- Good exposure: +0.1

**Target location:** `services/processing/src/segment_scorer.py`

### Deliverable Exporter (`processing/deliverable_exporter.py`)

FFmpeg-based segment trimming and report-ready export:

- `DeliverableExporter.trim_segment()` - Precise segment extraction at CRF 18
- `DeliverableExporter.export_report_ready()` - 1080p with timecode burn-in and source filename overlay

Filename convention: `<source>__seg###__<MM:SS>-<MM:SS>__<suffix>.mp4`

**Target location:** `services/processing/src/deliverable_exporter.py`

### Media Enhancements (`ingestion/media_enhancements.py`)

Detection and normalization utilities:

- `detect_device()` - Identify capture device from filename patterns (DJI, iPhone, GoPro, Meta, Insta360)
- `detect_hdr()` - Check color_transfer for HDR formats (PQ, HLG)
- `detect_vfr()` - Compare r_frame_rate vs avg_frame_rate (>1% delta = VFR)
- `build_tonemap_filter()` - Hable curve HDR-to-SDR via zscale
- `build_normalize_command()` - Full normalization FFmpeg command (H.264, yuv420p, 30fps CFR, loudnorm)
- `build_proxy_command()` - 1080p proxy generation command

**Target location:** `services/ingestion/src/media_enhancements.py`

### Database Migration (`migrations/add_quality_metrics.sql`)

PostgreSQL schema additions:

- `assets` table: `is_hdr`, `is_vfr`, `device_type`, `proxy_s3_path`, `color_transfer`
- `segments` table: `confidence`, `reason_tags` (JSONB), `notes`, `avg_blur`, `avg_brightness`, `avg_motion`, `deliverable_s3_path`
- New table: `frame_quality_metrics` (per-frame quality data)
- New table: `audio_analysis` (silence regions, peak levels)

**Target location:** `scripts/db_migrations/`

## Integration Steps

### 1. Copy schemas

```bash
cp flightdeck_contrib/schemas/quality.py \
   /path/to/flightdeck/shared/src/schemas/quality.py
```

Update `shared/src/schemas/__init__.py` to export the new models.

### 2. Copy processing modules

```bash
cp flightdeck_contrib/processing/quality_analyzer.py \
   /path/to/flightdeck/services/processing/src/quality_analyzer.py

cp flightdeck_contrib/processing/segment_scorer.py \
   /path/to/flightdeck/services/processing/src/segment_scorer.py

cp flightdeck_contrib/processing/deliverable_exporter.py \
   /path/to/flightdeck/services/processing/src/deliverable_exporter.py
```

### 3. Copy ingestion enhancements

```bash
cp flightdeck_contrib/ingestion/media_enhancements.py \
   /path/to/flightdeck/services/ingestion/src/media_enhancements.py
```

### 4. Run database migration

```bash
psql -d flightdeck -f flightdeck_contrib/migrations/add_quality_metrics.sql
```

### 5. Update imports

After copying, update import paths from `flightdeck_contrib.` to match
FlightDeck's module structure (e.g., `shared.schemas.quality`).

### 6. Wire into processing pipeline

Add quality analysis and segment scoring as new pipeline stages:

```
INGEST -> DETECT_DEVICE -> NORMALIZE -> PROXY -> STABILIZE -> ENHANCE ->
QUALITY_ANALYZE -> SEGMENT -> SCORE_SEGMENTS -> TRANSCODE ->
EXPORT_DELIVERABLES -> PACKAGE
```

### 7. Add API endpoints

- `POST /api/v1/quality/analyze/{asset_id}` - Start quality analysis
- `GET /api/v1/quality/report/{asset_id}` - Get quality report
- `POST /api/v1/deliverables/export` - Queue deliverable export
- `GET /api/v1/deliverables/{segment_id}` - Get deliverable status/URL

## Dependencies

Processing modules require:
- `opencv-python-headless>=4.9`
- `numpy>=1.26`
- `scenedetect[opencv]>=0.6`
- `pydantic>=2.0`
- FFmpeg (system binary)

## Async Usage

The OpenCV operations in `quality_analyzer.py` are synchronous. In FlightDeck's
async workers, wrap calls with:

```python
import asyncio

metrics = await asyncio.to_thread(
    analyzer.analyze_frames, video_path, output_dir
)
```

Or use Celery tasks for long-running analysis jobs.

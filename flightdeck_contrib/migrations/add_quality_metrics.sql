-- Migration: Add quality analysis support
-- Place in FlightDeck at: scripts/db_migrations/00X_quality_metrics.sql
--
-- This migration extends the existing assets and segments tables with quality
-- fields derived from Skyforge's analysis pipeline, and introduces two new
-- tables: frame_quality_metrics and audio_analysis.
--
-- Run order: after the base assets/segments/frames tables exist.
-- Safe to run multiple times (all statements use IF NOT EXISTS / IF EXISTS).

-- ---------------------------------------------------------------------------
-- Extend assets table
-- ---------------------------------------------------------------------------

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS is_hdr BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_vfr BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS device_type VARCHAR(50) DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS proxy_s3_path TEXT,
    ADD COLUMN IF NOT EXISTS color_transfer VARCHAR(50),
    ADD COLUMN IF NOT EXISTS pix_fmt VARCHAR(50);

-- ---------------------------------------------------------------------------
-- Extend segments table
-- ---------------------------------------------------------------------------

ALTER TABLE segments
    ADD COLUMN IF NOT EXISTS confidence FLOAT,
    ADD COLUMN IF NOT EXISTS reason_tags JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT '',
    ADD COLUMN IF NOT EXISTS avg_blur FLOAT,
    ADD COLUMN IF NOT EXISTS avg_brightness FLOAT,
    ADD COLUMN IF NOT EXISTS avg_motion FLOAT,
    ADD COLUMN IF NOT EXISTS has_audio BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS deliverable_s3_path TEXT;

-- ---------------------------------------------------------------------------
-- Per-frame quality metrics
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS frame_quality_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    frame_id        UUID REFERENCES frames(id) ON DELETE CASCADE,
    asset_id        UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    timestamp       FLOAT NOT NULL,
    blur_score      FLOAT NOT NULL,
    brightness      FLOAT NOT NULL,
    contrast        FLOAT NOT NULL,
    motion_score    FLOAT NOT NULL DEFAULT 0.0,
    quality_score   FLOAT NOT NULL DEFAULT 0.0,
    is_dark         BOOLEAN DEFAULT FALSE,
    is_overexposed  BOOLEAN DEFAULT FALSE,
    is_blurry       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_frame_quality_asset
    ON frame_quality_metrics (asset_id);

CREATE INDEX IF NOT EXISTS idx_frame_quality_score
    ON frame_quality_metrics (quality_score);

-- ---------------------------------------------------------------------------
-- Audio analysis results
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audio_analysis (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id            UUID UNIQUE NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    has_audio           BOOLEAN DEFAULT FALSE,
    silence_regions     JSONB DEFAULT '[]',
    audio_peaks         JSONB DEFAULT '[]',
    waveform_s3_path    TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Indexes for performance
-- ---------------------------------------------------------------------------

-- Segment confidence descending for "show best segments first" queries
CREATE INDEX IF NOT EXISTS idx_segments_confidence
    ON segments (confidence DESC NULLS LAST);

"""Transcode pipeline — create shareable versions of normalized footage."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from skyforge.core.media import VIDEO_EXTENSIONS, probe_file

# ============================================================================
# Preset configuration
# ============================================================================


@dataclass
class TranscodePreset:
    """Parameters for a single transcode target."""

    name: str
    description: str
    codec: str  # "h264" or "h265"
    crf: int
    max_width: int  # 0 = preserve source width
    encode_preset: str  # "veryfast", "medium", "slow"
    audio_bitrate: str

    @property
    def libcodec(self) -> str:
        """FFmpeg codec library name."""
        return "libx265" if self.codec == "h265" else "libx264"

    @property
    def scale_filter(self) -> str | None:
        """Build the -vf scale= expression, or None if no resize needed."""
        if self.max_width <= 0:
            return None
        # min(iw, W) avoids upscaling small sources; -2 keeps even dimensions
        return f"scale='min(iw,{self.max_width})':-2"


BUILTIN_PRESETS: dict[str, TranscodePreset] = {
    "web": TranscodePreset(
        name="web",
        description="720p H.265 — compact for social/web delivery",
        codec="h265",
        crf=28,
        max_width=1280,
        encode_preset="medium",
        audio_bitrate="128k",
    ),
    "review": TranscodePreset(
        name="review",
        description="1080p H.264 — client review, max compatibility",
        codec="h264",
        crf=26,
        max_width=1920,
        encode_preset="veryfast",
        audio_bitrate="192k",
    ),
    "archive": TranscodePreset(
        name="archive",
        description="Source resolution H.265 — space-efficient storage",
        codec="h265",
        crf=24,
        max_width=0,
        encode_preset="slow",
        audio_bitrate="256k",
    ),
    "mobile": TranscodePreset(
        name="mobile",
        description="480p H.264 — small file for mobile preview",
        codec="h264",
        crf=30,
        max_width=854,
        encode_preset="veryfast",
        audio_bitrate="96k",
    ),
}


def load_presets(
    extra: dict[str, TranscodePreset] | None = None,
) -> dict[str, TranscodePreset]:
    """Return merged preset registry (built-in + optional extras)."""
    presets = dict(BUILTIN_PRESETS)
    if extra:
        presets.update(extra)
    return presets


# ============================================================================
# Result tracking
# ============================================================================


@dataclass
class TranscodeResult:
    """Result of transcoding a single file."""

    source: Path
    output: Path | None = None
    preset: str = ""
    skipped: bool = False
    error: str | None = None
    input_size_bytes: int = 0
    output_size_bytes: int = 0

    @property
    def size_reduction_pct(self) -> float | None:
        """Percentage reduction from input to output size."""
        if self.input_size_bytes > 0 and self.output_size_bytes > 0:
            return 100 * (1 - self.output_size_bytes / self.input_size_bytes)
        return None


# ============================================================================
# FFmpeg command building
# ============================================================================


def build_transcode_command(
    source: Path,
    output: Path,
    preset: TranscodePreset,
    has_audio: bool,
) -> list[str]:
    """Build the FFmpeg command list for a given transcode preset."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(source)]

    # Video filter (scale only — source is already SDR/CFR from ingest)
    scale = preset.scale_filter
    if scale:
        cmd.extend(["-vf", scale])

    # Video codec
    cmd.extend(
        [
            "-c:v",
            preset.libcodec,
            "-pix_fmt",
            "yuv420p",
            "-preset",
            preset.encode_preset,
            "-crf",
            str(preset.crf),
        ]
    )

    # H.265: tag as hvc1 for Apple/QuickTime compatibility
    if preset.codec == "h265":
        cmd.extend(["-tag:v", "hvc1"])

    # Audio
    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", preset.audio_bitrate])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


# ============================================================================
# Transcode execution
# ============================================================================


def transcode_file(
    source: Path,
    output_dir: Path,
    preset: TranscodePreset,
    skip_existing: bool = True,
    dry_run: bool = False,
) -> TranscodeResult:
    """Transcode a single normalized video file.

    Output filename: <stem>_<preset_name>.mp4
    """
    result = TranscodeResult(
        source=source,
        preset=preset.name,
        input_size_bytes=source.stat().st_size,
    )

    output_name = f"{source.stem}_{preset.name}.mp4"
    output_path = output_dir / output_name
    result.output = output_path

    if skip_existing and output_path.exists():
        result.skipped = True
        result.output_size_bytes = output_path.stat().st_size
        return result

    if dry_run:
        return result

    output_dir.mkdir(parents=True, exist_ok=True)

    info = probe_file(source)
    cmd = build_transcode_command(source, output_path, preset, info.has_audio)

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if output_path.exists():
            result.output_size_bytes = output_path.stat().st_size
    except subprocess.CalledProcessError as e:
        stderr_tail = e.stderr[-500:] if e.stderr else ""
        result.error = f"FFmpeg failed (rc={e.returncode}): {stderr_tail}"
    except subprocess.TimeoutExpired:
        result.error = "FFmpeg timed out (>60min)"

    return result


def transcode_project(
    norm_dir: Path,
    output_dir: Path,
    preset: TranscodePreset,
    skip_existing: bool = True,
    dry_run: bool = False,
    progress_callback: object = None,
) -> list[TranscodeResult]:
    """Walk 02_NORMALIZED/ and transcode every video file.

    Mirrors device-subfolder structure under 06_TRANSCODED/<preset>/.
    """
    results: list[TranscodeResult] = []
    preset_output_dir = output_dir / preset.name

    device_dirs = sorted(d for d in norm_dir.iterdir() if d.is_dir())

    for device_dir in device_dirs:
        device_out = preset_output_dir / device_dir.name

        videos = sorted(
            f for f in device_dir.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        )

        for video in videos:
            if progress_callback:
                progress_callback(video, device_dir.name)

            result = transcode_file(
                video,
                device_out,
                preset,
                skip_existing,
                dry_run,
            )
            results.append(result)

    return results


# ============================================================================
# Manifest
# ============================================================================


def generate_transcode_manifest(
    results: list[TranscodeResult],
    output: Path,
) -> None:
    """Write a JSON manifest of transcode results."""
    entries = []
    for r in results:
        reduction = r.size_reduction_pct
        entries.append(
            {
                "source": str(r.source),
                "output": str(r.output) if r.output else None,
                "preset": r.preset,
                "skipped": r.skipped,
                "error": r.error,
                "input_size_mb": round(r.input_size_bytes / (1024 * 1024), 1),
                "output_size_mb": (
                    round(r.output_size_bytes / (1024 * 1024), 1) if r.output_size_bytes else None
                ),
                "size_reduction_pct": (round(reduction, 1) if reduction is not None else None),
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(entries, indent=2))

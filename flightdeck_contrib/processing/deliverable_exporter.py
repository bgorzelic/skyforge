"""Deliverable export — segment trimming and report-ready timecode burn-in.

Extracted from Skyforge's core/exporter.py for FlightDeck integration.
Place in FlightDeck at: processing/src/deliverable_exporter.py

Generates two output types:
- Trimmed selects: lossless-ish H.264 at high quality (crf 18) for editing.
- Report-ready clips: 1080p H.264 with burned-in timecode and filename overlay,
  suitable for client review without an NLE.

All FFmpeg commands are ported directly from Skyforge's exporter.py. The output
filename convention mirrors Skyforge's for cross-system compatibility:
    <source>__seg###__<MM:SS>-<MM:SS>__<tags>.mp4

Dependencies: ffmpeg (system binary), flightdeck_contrib.schemas.quality
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from flightdeck_contrib.schemas.quality import DeliverableRequest


class DeliverableExporter:
    """Trims video segments and creates report-ready deliverables via FFmpeg.

    Example usage in FlightDeck worker::

        exporter = DeliverableExporter()
        output = exporter.export_report_ready(request, output_dir=Path("/tmp/exports"))
        if output:
            s3_path = await upload_to_s3(output)
    """

    def trim_segment(
        self,
        request: DeliverableRequest,
        output_dir: Path,
        crf: int = 18,
    ) -> Path | None:
        """Trim a selected segment from its source video.

        Uses stream-copy-safe re-encoding to H.264/yuv420p at CFR 30fps.
        Skips if the output file already exists (idempotent).

        Output filename format::
            <source_stem>__seg###__<MM:SS>-<MM:SS>__<tags>.mp4

        Args:
            request: DeliverableRequest describing the segment to export.
            output_dir: Directory to write the trimmed segment into.
            crf: H.264 constant rate factor (lower = higher quality). Default 18.

        Returns:
            Path to the output file, or None if FFmpeg failed.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        source = Path(request.source_path)

        filename = self.build_export_filename(
            source_name=source.stem,
            segment_id=int(request.segment_id) if request.segment_id.isdigit() else 0,
            start=request.start_time,
            end=request.end_time,
            suffix="clip",
        )
        output = output_dir / filename

        if output.exists():
            return output

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-ss",
            str(request.start_time),
            "-i",
            str(source),
            "-t",
            str(request.duration),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-fps_mode",
            "cfr",
            "-r",
            "30",
        ]

        if request.has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "256k"])
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output)])
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if output.exists():
            return output
        return None

    def export_report_ready(
        self,
        request: DeliverableRequest,
        output_dir: Path,
    ) -> Path | None:
        """Create a report-ready clip with burned-in timecode and filename.

        Scales to target_width (default 1920), applies a drawtext timecode
        overlay showing original source time in the lower-left, and optionally
        burns the source filename in the upper-left.

        FFmpeg filter chain ported directly from Skyforge's exporter.py:74-91.
        Skips if the output file already exists (idempotent).

        Args:
            request: DeliverableRequest describing the segment and burn-in settings.
            output_dir: Directory to write the report clip into.

        Returns:
            Path to the output file, or None if FFmpeg failed.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        source = Path(request.source_path)
        src_stem = source.stem.replace("_norm", "")

        source_label = request.source_label or src_stem
        filename = self.build_export_filename(
            source_name=src_stem,
            segment_id=int(request.segment_id) if request.segment_id.isdigit() else 0,
            start=request.start_time,
            end=request.end_time,
            suffix="report",
        )
        output = output_dir / filename

        if output.exists():
            return output

        # Build video filter chain
        filters: list[str] = [f"scale={request.target_width}:-2"]

        if request.burn_timecode:
            tc_offset = request.start_time
            filters.append(
                f"drawtext=text='%{{pts\\:hms\\:{tc_offset}}}'"
                f":fontsize=24:fontcolor=white:borderw=2:bordercolor=black"
                f":x=10:y=h-40"
            )

        if request.burn_filename:
            filters.append(
                f"drawtext=text='{source_label}'"
                f":fontsize=18:fontcolor=white@0.7:borderw=1:bordercolor=black"
                f":x=10:y=10"
            )

        vf = ",".join(filters)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-ss",
            str(request.start_time),
            "-i",
            str(source),
            "-t",
            str(request.duration),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            str(request.crf),
            "-fps_mode",
            "cfr",
            "-r",
            "30",
        ]

        if request.has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output)])
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if output.exists():
            return output
        return None

    @staticmethod
    def build_export_filename(
        source_name: str,
        segment_id: int,
        start: float,
        end: float,
        suffix: str = "clip",
    ) -> str:
        """Build a standardized export filename.

        Format: ``<source>__seg###__<MM:SS>-<MM:SS>__<suffix>.mp4``

        This convention is shared with Skyforge so files produced by either
        system are immediately identifiable by name.

        Args:
            source_name: Stem of the source file (no extension, no _norm suffix).
            segment_id: Numeric segment identifier.
            start: Segment start time in seconds.
            end: Segment end time in seconds.
            suffix: Descriptive suffix (e.g. "clip", "report", or joined tags).

        Returns:
            Filename string with .mp4 extension.
        """
        clean_source = source_name.replace("_norm", "")
        start_str = _time_str(start)
        end_str = _time_str(end)
        return f"{clean_source}__seg{segment_id:03d}__{start_str}-{end_str}__{suffix}.mp4"


# ============================================================================
# Internal helpers
# ============================================================================


def _time_str(seconds: float) -> str:
    """Format seconds as MM:SS suitable for filenames (e.g. 01m30s)."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}m{s:02d}s"

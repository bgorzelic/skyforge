"""Export pipeline — trim selects and create report-ready deliverables."""

import subprocess
from pathlib import Path

from skyforge.core.selector import Segment


def trim_segment(
    segment: Segment,
    output_dir: Path,
    crf: int = 18,
) -> Path | None:
    """Trim a selected segment from source video.

    Output filename: <source>__seg###__<start>-<end>__<tags>.mp4
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Path(segment.source_file)

    # Build filename
    src_stem = source.stem.replace("_norm", "")
    start_str = _time_str(segment.start_time)
    end_str = _time_str(segment.end_time)
    tag_str = "_".join(segment.reason_tags[:3]) if segment.reason_tags else "clip"
    filename = f"{src_stem}__seg{segment.segment_id:03d}__{start_str}-{end_str}__{tag_str}.mp4"
    output = output_dir / filename

    if output.exists():
        return output

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss",
        str(segment.start_time),
        "-i",
        str(source),
        "-t",
        str(segment.duration),
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

    if segment.has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "256k"])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-movflags", "+faststart", str(output)])
    subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if output.exists():
        return output
    return None


def export_report_ready(
    segment: Segment,
    output_dir: Path,
    width: int = 1920,
    burn_timecode: bool = True,
) -> Path | None:
    """Create a report-ready version: 1080p with burned-in timecode."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Path(segment.source_file)

    src_stem = source.stem.replace("_norm", "")
    start_str = _time_str(segment.start_time)
    end_str = _time_str(segment.end_time)
    filename = f"{src_stem}__seg{segment.segment_id:03d}__{start_str}-{end_str}__report.mp4"
    output = output_dir / filename

    if output.exists():
        return output

    # Build video filter
    filters = [f"scale={width}:-2"]
    if burn_timecode:
        # Burn timecode showing original source time
        tc_offset = segment.start_time
        filters.append(
            f"drawtext=text='%{{pts\\:hms\\:{tc_offset}}}'"
            f":fontsize=24:fontcolor=white:borderw=2:bordercolor=black"
            f":x=10:y=h-40"
        )
        # Also burn source filename
        filters.append(
            f"drawtext=text='{src_stem}'"
            f":fontsize=18:fontcolor=white@0.7:borderw=1:bordercolor=black"
            f":x=10:y=10"
        )

    vf = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss",
        str(segment.start_time),
        "-i",
        str(source),
        "-t",
        str(segment.duration),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-fps_mode",
        "cfr",
        "-r",
        "30",
    ]

    if segment.has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-movflags", "+faststart", str(output)])
    subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if output.exists():
        return output
    return None


def _time_str(seconds: float) -> str:
    """Format seconds as MM-SS for filenames."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}m{s:02d}s"

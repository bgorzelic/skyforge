"""FFmpeg ingest pipeline — normalize, transcode, and generate proxies."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from skyforge.core.media import MediaInfo, probe_file, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS


@dataclass
class PipelineConfig:
    """Configuration for the ingest pipeline."""

    target_fps: int = 30
    crf: int = 18
    proxy_crf: int = 28
    proxy_scale: str = "1920:-2"
    audio_bitrate: str = "256k"
    proxy_audio_bitrate: str = "128k"
    audio_normalize: bool = True
    skip_proxies: bool = False
    skip_existing: bool = True
    dry_run: bool = False

    @property
    def audio_filter(self) -> str:
        if self.audio_normalize:
            return "loudnorm=I=-16:TP=-1.5:LRA=11"
        return ""

    @property
    def tonemap_filter(self) -> str:
        return (
            "zscale=t=linear:npl=100,"
            "tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,"
            "format=yuv420p"
        )


@dataclass
class ProcessingResult:
    """Result of processing a single file."""

    source: Path
    normalized: Path | None = None
    proxy: Path | None = None
    skipped: bool = False
    error: str | None = None
    device: str = "unknown"
    hdr_tonemapped: bool = False


def process_video(
    source: Path,
    norm_dir: Path,
    proxy_dir: Path,
    config: PipelineConfig,
    device: str = "unknown",
) -> ProcessingResult:
    """Normalize a single video file and optionally generate a proxy."""
    result = ProcessingResult(source=source, device=device)
    info = probe_file(source)

    base = source.stem
    norm_path = norm_dir / f"{base}_norm.mp4"
    proxy_path = proxy_dir / f"{base}_proxy.mp4"
    result.normalized = norm_path
    result.proxy = proxy_path

    # Skip if already done
    if config.skip_existing and norm_path.exists():
        result.skipped = True
        if not config.skip_proxies and not proxy_path.exists():
            result.skipped = False
        else:
            return result

    if config.dry_run:
        return result

    # Build normalize command
    try:
        _run_normalize(source, norm_path, info, config)
        result.hdr_tonemapped = info.is_hdr
    except subprocess.CalledProcessError as e:
        result.error = f"Normalize failed: {e.returncode}"
        return result

    # Generate proxy
    if not config.skip_proxies:
        try:
            _run_proxy(norm_path, proxy_path, config)
        except subprocess.CalledProcessError as e:
            result.error = f"Proxy failed: {e.returncode}"

    return result


def process_image(source: Path, norm_dir: Path, config: PipelineConfig) -> ProcessingResult:
    """Copy/convert an image file to the normalized directory."""
    result = ProcessingResult(source=source)
    dest = norm_dir / source.name

    if config.skip_existing and dest.exists():
        result.skipped = True
        return result

    if config.dry_run:
        result.normalized = dest
        return result

    # For images, just copy (future: convert RAW to TIFF/JPEG)
    import shutil
    shutil.copy2(source, dest)
    result.normalized = dest
    return result


def run_pipeline(
    raw_dir: Path,
    norm_dir: Path,
    proxy_dir: Path,
    config: PipelineConfig,
    progress_callback=None,
) -> list[ProcessingResult]:
    """Run the full ingest pipeline on a raw directory.

    Processes all device subdirectories automatically.
    """
    results = []

    # Process each device subdirectory
    device_dirs = sorted([d for d in raw_dir.iterdir() if d.is_dir()])

    for device_dir in device_dirs:
        device_name = device_dir.name
        device_norm = norm_dir / device_name
        device_proxy = proxy_dir / device_name
        device_norm.mkdir(parents=True, exist_ok=True)
        device_proxy.mkdir(parents=True, exist_ok=True)

        # Collect media files (recursive — handles nested DCIM/ structures)
        files = sorted([
            f for f in device_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)
        ])

        for f in files:
            if progress_callback:
                progress_callback(f, device_name)

            if f.suffix.lower() in VIDEO_EXTENSIONS:
                result = process_video(f, device_norm, device_proxy, config, device=device_name)
            else:
                result = process_image(f, device_norm, config)
                result.device = device_name

            results.append(result)

        # Copy telemetry/SRT files (recursive)
        import shutil
        for srt in device_dir.rglob("*.SRT"):
            dest = device_norm / srt.name
            if not dest.exists():
                shutil.copy2(srt, dest)
        for srt in device_dir.rglob("*.srt"):
            dest = device_norm / srt.name
            if not dest.exists():
                shutil.copy2(srt, dest)

    return results


def generate_manifest(results: list[ProcessingResult], output: Path) -> None:
    """Generate a JSON manifest of all processed files."""
    entries = []
    for r in results:
        entry = {
            "device": r.device,
            "original": str(r.source),
            "normalized": str(r.normalized) if r.normalized else None,
            "proxy": str(r.proxy) if r.proxy and r.proxy.exists() else None,
            "skipped": r.skipped,
            "error": r.error,
            "hdr_tonemapped": r.hdr_tonemapped,
        }
        # Add resolution/fps info if normalized file exists
        if r.normalized and r.normalized.exists():
            info = probe_file(r.normalized)
            entry["resolution"] = info.resolution
            entry["fps"] = round(info.fps, 2)
            entry["duration_s"] = round(info.duration, 1)

        entries.append(entry)

    output.write_text(json.dumps(entries, indent=2))


# ============================================================================
# Internal FFmpeg runners
# ============================================================================

def _run_normalize(source: Path, output: Path, info: MediaInfo, config: PipelineConfig) -> None:
    """Run FFmpeg to normalize a video file."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(source)]

    # Video filter for HDR tonemap
    if info.is_hdr:
        cmd.extend(["-vf", config.tonemap_filter])

    # CFR + target fps
    cmd.extend(["-fps_mode", "cfr", "-r", str(config.target_fps)])

    # H.264 encoding
    cmd.extend([
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", str(config.crf),
        "-x264-params", "keyint=60:min-keyint=60:scenecut=0",
    ])

    # Audio
    if info.has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", config.audio_bitrate])
        if config.audio_normalize and config.audio_filter:
            cmd.extend(["-af", config.audio_filter])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-movflags", "+faststart", str(output)])
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _run_proxy(source: Path, output: Path, config: PipelineConfig) -> None:
    """Run FFmpeg to generate a lightweight proxy."""
    info = probe_file(source)
    cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", str(source),
        "-vf", f"scale={config.proxy_scale}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", str(config.proxy_crf),
    ]
    if info.has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", config.proxy_audio_bitrate])
    else:
        cmd.extend(["-an"])
    cmd.extend(["-movflags", "+faststart", str(output)])
    subprocess.run(cmd, check=True, capture_output=True, text=True)

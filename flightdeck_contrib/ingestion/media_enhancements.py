"""Media detection and FFmpeg command builders for FlightDeck ingestion.

Extracted from Skyforge's core/media.py and core/pipeline.py for FlightDeck
integration. Place in FlightDeck at: ingestion/src/media_enhancements.py

These utilities extend FlightDeck's existing ingestion/src/media.py with:
- Device detection from filename patterns (DJI, iPhone, GoPro, Meta glasses, etc.)
- HDR detection from color_transfer metadata
- VFR (variable frame rate) detection
- FFmpeg command builders for normalization and proxy generation

Integration note: Import these functions alongside FlightDeck's existing
probe_file() in ingestion/src/media.py, or call them in the ingestion worker
after probing to populate the new DB columns added in migrations/add_quality_metrics.sql.

Dependencies: ffmpeg (system binary)
"""

from __future__ import annotations

from pathlib import Path

# Known HDR color transfer functions reported by ffprobe
_HDR_TRANSFER_FUNCTIONS = frozenset({"smpte2084", "arib-std-b67"})

# VFR detection tolerance: if nominal vs average fps differ by more than 1%
_VFR_TOLERANCE = 0.01


def detect_device(file_path: Path) -> str:
    """Detect the capture device from filename and path conventions.

    Matches against known directory and filename patterns for each device type.
    Covers DJI drones (PTSC_* prefix, ATOM_001 directory), iPhone (IMG_*,
    APPLE/IPHONE directory), GoPro (GH/GX prefix), Meta Ray-Ban glasses
    (META/RAY-BAN directory or SINGULAR_DISPLAY filename), and Insta360.

    Integrate with FlightDeck by calling after probe_file()::

        device = detect_device(Path(asset.original_path))
        await db.execute(
            "UPDATE assets SET device_type = $1 WHERE id = $2",
            device, asset.id
        )

    Args:
        file_path: Absolute path to the media file.

    Returns:
        Device string: "drone", "iphone", "gopro", "dji", "meta_glasses",
        "insta360", or "unknown".
    """
    parts = [p.upper() for p in file_path.parts]
    name = file_path.stem.upper()

    if any(d in parts for d in ("ATOM_001", "ATOM", "DCIM")) or name.startswith("PTSC_"):
        return "drone"
    if "IPHONE" in parts or "APPLE" in parts:
        return "iphone"
    if "META_GLASSES" in parts or "META" in parts or "RAY-BAN" in parts:
        return "meta_glasses"
    if "GOPRO" in parts or name.startswith("GH") or name.startswith("GX"):
        return "gopro"
    if "DJI" in parts or name.startswith("DJI_"):
        return "dji"
    if "INSTA360" in parts:
        return "insta360"
    if "SINGULAR_DISPLAY" in name:
        return "meta_glasses"

    return "unknown"


def detect_hdr(color_transfer: str) -> bool:
    """Detect HDR content from the color_transfer field reported by ffprobe.

    Recognizes PQ (Perceptual Quantizer / SMPTE ST 2084, used by DJI D-Log M
    and most 10-bit HDR) and HLG (Hybrid Log-Gamma / ARIB STD-B67).

    Integrate with FlightDeck's probe result::

        is_hdr = detect_hdr(ffprobe_stream.get("color_transfer", ""))
        await db.execute(
            "UPDATE assets SET is_hdr = $1 WHERE id = $2",
            is_hdr, asset.id
        )

    Args:
        color_transfer: Value of the color_transfer field from ffprobe stream data.

    Returns:
        True if the color transfer function indicates HDR content.
    """
    return color_transfer.lower() in _HDR_TRANSFER_FUNCTIONS


def detect_vfr(r_frame_rate: str, avg_frame_rate: str) -> bool:
    """Detect variable frame rate (VFR) by comparing nominal vs average FPS.

    A difference greater than 1% between the container's nominal frame rate
    (r_frame_rate) and the actual average frame rate (avg_frame_rate) indicates
    VFR content. iPhone H.265 video from iOS 17+ frequently records at VFR.

    Integrate with FlightDeck's probe result::

        is_vfr = detect_vfr(
            stream.get("r_frame_rate", "0/1"),
            stream.get("avg_frame_rate", "0/1"),
        )

    Args:
        r_frame_rate: Nominal (container) frame rate as a fraction string, e.g. "30000/1001".
        avg_frame_rate: Actual average frame rate as a fraction string, e.g. "29970/1000".

    Returns:
        True if the difference exceeds the 1% VFR tolerance threshold.
    """
    r_fps = parse_fraction(r_frame_rate)
    avg_fps = parse_fraction(avg_frame_rate)

    if r_fps <= 0 or avg_fps <= 0:
        return False

    return abs(r_fps - avg_fps) / r_fps > _VFR_TOLERANCE


def parse_fraction(frac_str: str) -> float:
    """Parse a frame rate fraction string into a float.

    Handles both fraction format ("30000/1001") and plain float format ("30.0").
    Returns 0.0 on any parse error to allow safe downstream comparisons.

    Args:
        frac_str: Frame rate string as returned by ffprobe (e.g. "30000/1001").

    Returns:
        Float frame rate, or 0.0 if unparseable or denominator is zero.
    """
    try:
        if "/" in frac_str:
            num, den = frac_str.split("/", 1)
            denominator = float(den)
            if denominator == 0:
                return 0.0
            return float(num) / denominator
        return float(frac_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


def build_tonemap_filter() -> str:
    """Build the HDR-to-SDR Hable tonemapping FFmpeg filter chain.

    Uses zscale for colorspace conversion (more accurate than FFmpeg's built-in
    colormatrix) with Hable tonemapping for natural-looking SDR output from
    PQ/HLG HDR sources. This is the same filter used in Skyforge's ingest pipeline.

    The filter chain:
    1. zscale to linear light (npl=100 for SDR brightness normalization)
    2. Hable tonemap (desat=0 preserves saturation)
    3. zscale to BT.709 for standard SDR delivery
    4. format=yuv420p for H.264 compatibility

    Integrate with FlightDeck's FFmpeg command builders::

        if asset.is_hdr:
            cmd.extend(["-vf", build_tonemap_filter()])

    Returns:
        FFmpeg -vf filter string for HDR tonemapping.
    """
    return (
        "zscale=t=linear:npl=100,"
        "tonemap=tonemap=hable:desat=0,"
        "zscale=t=bt709:m=bt709:r=tv,"
        "format=yuv420p"
    )


def build_normalize_command(
    source: Path,
    output: Path,
    is_hdr: bool = False,
    has_audio: bool = True,
    target_fps: int = 30,
    crf: int = 18,
    audio_normalize: bool = True,
) -> list[str]:
    """Build the FFmpeg command for normalizing a video to the FlightDeck baseline.

    Baseline specification:
    - Container: MP4 (faststart for streaming)
    - Video codec: H.264 (libx264), yuv420p
    - Frame rate: CFR at target_fps (default 30)
    - HDR: Hable tonemapped to SDR if is_hdr is True
    - Audio: AAC 256k, optionally loudnorm normalized to -16 LUFS
    - Keyframe interval: every 60 frames (2s at 30fps) for seek accuracy

    This mirrors Skyforge's pipeline._run_normalize() exactly. The resulting
    file is suitable for frame-accurate seeking in analysis workers.

    Integrate with FlightDeck's ingestion worker::

        cmd = build_normalize_command(
            source=tmp_path,
            output=norm_path,
            is_hdr=asset.is_hdr,
            has_audio=asset.has_audio,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

    Args:
        source: Local path to the source video file.
        output: Local path for the normalized output file.
        is_hdr: If True, applies HDR tonemapping before encoding.
        has_audio: If True, encodes audio; otherwise strips it (-an).
        target_fps: Target constant frame rate. Default 30.
        crf: H.264 CRF quality (lower = higher quality). Default 18.
        audio_normalize: If True, applies loudnorm to -16 LUFS.

    Returns:
        List of strings suitable for subprocess.run().
    """
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(source)]

    if is_hdr:
        cmd.extend(["-vf", build_tonemap_filter()])

    cmd.extend(["-fps_mode", "cfr", "-r", str(target_fps)])

    cmd.extend([
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", str(crf),
        "-x264-params", "keyint=60:min-keyint=60:scenecut=0",
    ])

    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "256k"])
        if audio_normalize:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd


def build_proxy_command(
    source: Path,
    output: Path,
    has_audio: bool = True,
    scale: str = "1920:-2",
    crf: int = 28,
    audio_bitrate: str = "128k",
) -> list[str]:
    """Build the FFmpeg command for generating a lightweight editing proxy.

    Proxies are 1080p (or custom scale) H.264 files intended for non-linear
    editing or preview playback. They are not deliverable quality — use
    build_normalize_command() or export_report_ready() for deliverables.

    Integrate with FlightDeck's ingestion worker::

        cmd = build_proxy_command(norm_path, proxy_path, has_audio=asset.has_audio)
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        await db.execute(
            "UPDATE assets SET proxy_s3_path = $1 WHERE id = $2",
            s3_upload(proxy_path), asset.id
        )

    Args:
        source: Local path to the normalized (or source) video file.
        output: Local path for the proxy output file.
        has_audio: If True, encodes audio at audio_bitrate; otherwise strips it.
        scale: FFmpeg scale filter value. Default "1920:-2" (1080p, keep AR).
        crf: H.264 CRF. Higher = smaller files. Default 28.
        audio_bitrate: AAC audio bitrate for the proxy. Default "128k".

    Returns:
        List of strings suitable for subprocess.run().
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-vf",
        f"scale={scale}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
    ]

    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate])
    else:
        cmd.extend(["-an"])

    cmd.extend(["-movflags", "+faststart", str(output)])
    return cmd

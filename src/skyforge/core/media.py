"""Media file handling, detection, and metadata extraction via ffprobe."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".m2ts"}
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".dng",
    ".raw",
    ".tiff",
    ".tif",
    ".heic",
    ".cr2",
    ".arw",
    ".nef",
}
TELEMETRY_EXTENSIONS = {".srt", ".csv", ".gpx", ".kml"}
PROXY_EXTENSIONS = {".lrv"}
THUMBNAIL_EXTENSIONS = {".thm"}

ALL_MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS


@dataclass
class MediaInfo:
    """Metadata extracted from a media file via ffprobe."""

    path: Path
    codec: str = "unknown"
    width: int = 0
    height: int = 0
    fps: float = 0.0
    avg_fps: float = 0.0
    duration: float = 0.0
    size_bytes: int = 0
    pix_fmt: str = "unknown"
    color_transfer: str = "unknown"
    color_primaries: str = "unknown"
    has_audio: bool = False
    is_hdr: bool = False
    is_vfr: bool = False
    device: str = "unknown"
    media_type: str = "unknown"  # video, image, telemetry, proxy, thumbnail
    gps: tuple[float, float] | None = None  # (latitude, longitude) from EXIF

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 * 1024 * 1024)

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "unknown"

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def is_4k(self) -> bool:
        return max(self.width, self.height) >= 3840


def extract_gps_from_image(path: Path) -> tuple[float, float] | None:
    """Extract GPS coordinates from image EXIF data.

    Returns (latitude, longitude) in decimal degrees, or None if unavailable.
    Requires Pillow (pip install skyforge[ai]).
    """
    try:
        from PIL import Image
        from PIL.ExifTags import GPSTAGS, TAGS
    except ImportError:
        return None

    try:
        img = Image.open(path)
        exif_data = img._getexif()
        if not exif_data:
            return None
    except Exception:
        return None

    # Find GPSInfo tag
    gps_info = {}
    for tag_id, value in exif_data.items():
        tag = TAGS.get(tag_id, tag_id)
        if tag == "GPSInfo":
            for gps_tag_id, gps_value in value.items():
                gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                gps_info[gps_tag] = gps_value
            break

    if not gps_info:
        return None

    def _dms_to_decimal(dms: tuple, ref: str) -> float | None:
        """Convert degrees/minutes/seconds to decimal degrees."""
        try:
            degrees = float(dms[0])
            minutes = float(dms[1])
            seconds = float(dms[2])
            decimal = degrees + minutes / 60 + seconds / 3600
            if ref in ("S", "W"):
                decimal = -decimal
            return decimal
        except (TypeError, ValueError, IndexError):
            return None

    lat = _dms_to_decimal(gps_info.get("GPSLatitude", ()), gps_info.get("GPSLatitudeRef", "N"))
    lon = _dms_to_decimal(gps_info.get("GPSLongitude", ()), gps_info.get("GPSLongitudeRef", "E"))

    if lat is not None and lon is not None:
        return (round(lat, 6), round(lon, 6))
    return None


def probe_file(path: Path) -> MediaInfo:
    """Extract metadata from a media file using ffprobe."""
    info = MediaInfo(path=path, size_bytes=path.stat().st_size)
    info.media_type = _classify_type(path)
    info.device = detect_device(path)

    if info.media_type != "video":
        return info

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,"
                "pix_fmt,color_transfer,color_primaries,duration",
                "-show_entries",
                "format=duration,size",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return info

    # Parse video stream
    streams = data.get("streams", [])
    if streams:
        vs = streams[0]
        info.codec = vs.get("codec_name", "unknown")
        info.width = int(vs.get("width", 0))
        info.height = int(vs.get("height", 0))
        info.pix_fmt = vs.get("pix_fmt", "unknown")
        info.color_transfer = vs.get("color_transfer", "unknown")
        info.color_primaries = vs.get("color_primaries", "unknown")

        # Parse frame rates
        r_fps = _parse_fraction(vs.get("r_frame_rate", "0/1"))
        avg_fps = _parse_fraction(vs.get("avg_frame_rate", "0/1"))
        info.fps = r_fps
        info.avg_fps = avg_fps

        # VFR detection: significant difference between nominal and average fps
        if r_fps > 0 and avg_fps > 0:
            info.is_vfr = abs(r_fps - avg_fps) / r_fps > 0.01

        # HDR detection
        info.is_hdr = info.color_transfer in ("smpte2084", "arib-std-b67")

        # Duration
        dur = vs.get("duration") or data.get("format", {}).get("duration")
        if dur:
            info.duration = float(dur)

    # Check for audio streams
    try:
        audio_result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        info.has_audio = len(audio_result.stdout.strip()) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return info


def scan_directory(directory: Path, recursive: bool = True) -> list[MediaInfo]:
    """Scan a directory for all media files and probe each one."""
    all_extensions = (
        VIDEO_EXTENSIONS
        | IMAGE_EXTENSIONS
        | TELEMETRY_EXTENSIONS
        | PROXY_EXTENSIONS
        | THUMBNAIL_EXTENSIONS
    )
    pattern = "**/*" if recursive else "*"
    files = [
        f for f in directory.glob(pattern) if f.is_file() and f.suffix.lower() in all_extensions
    ]
    results = []
    for f in sorted(files):
        info = probe_file(f)
        if info.media_type == "image":
            info.gps = extract_gps_from_image(f)
        results.append(info)
    return results


def detect_device(file_path: Path) -> str:
    """Detect the capture device from the file path or naming convention."""
    parts = [p.upper() for p in file_path.parts]
    name = file_path.stem.upper()

    # Known device patterns
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


def _classify_type(path: Path) -> str:
    """Classify a file by its extension."""
    ext = path.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in TELEMETRY_EXTENSIONS:
        return "telemetry"
    if ext in PROXY_EXTENSIONS:
        return "proxy"
    if ext in THUMBNAIL_EXTENSIONS:
        return "thumbnail"
    return "unknown"


def _parse_fraction(frac_str: str) -> float:
    """Parse a fraction string like '30000/1001' into a float."""
    try:
        if "/" in frac_str:
            num, den = frac_str.split("/")
            den = float(den)
            if den == 0:
                return 0.0
            return float(num) / den
        return float(frac_str)
    except (ValueError, ZeroDivisionError):
        return 0.0

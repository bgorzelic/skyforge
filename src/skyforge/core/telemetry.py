"""SRT telemetry parser — extract GPS, camera, and flight data from drone SRT files."""

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TelemetryFrame:
    """A single telemetry data point from a drone SRT file."""

    index: int
    timestamp_start: str
    timestamp_end: str
    seconds: float
    f_stop: float | None = None
    shutter_speed: str | None = None
    iso: int | None = None
    ev: float | None = None
    height_m: float | None = None
    distance_m: float | None = None
    horizontal_speed_ms: float | None = None
    descent_speed_ms: float | None = None
    longitude: float | None = None
    latitude: float | None = None
    zoom: float | None = None

    @property
    def gps(self) -> tuple[float, float] | None:
        if self.longitude is not None and self.latitude is not None:
            return (self.latitude, self.longitude)
        return None

    @property
    def altitude_ft(self) -> float | None:
        if self.height_m is not None:
            return self.height_m * 3.28084
        return None

    @property
    def speed_mph(self) -> float | None:
        if self.horizontal_speed_ms is not None:
            return self.horizontal_speed_ms * 2.23694
        return None


# Pattern for ATOM drone SRT telemetry lines
# Example: F1.8 SS:1/1603 ISO: 114 EV:0.0 H:1.2m D:0.1m
#   HS:0.0m/s DS:0.1m/s GPS:(-119.937027,37.568775) ZOOM:1.10X
_TELEMETRY_PATTERN = re.compile(
    r"F([\d.]+)\s+"
    r"SS:([\d/]+)\s+"
    r"ISO:\s*(\d+)\s+"
    r"EV:([-\d.]+)\s+"
    r"H:([\d.]+)m\s+"
    r"D:([\d.]+)m\s+"
    r"HS:([\d.]+)m/s\s+"
    r"DS:([\d.]+)m/s\s+"
    r"GPS:\(([-\d.]+),([-\d.]+)\)\s*"
    r"ZOOM:([\d.]+)X"
)

_TIMESTAMP_PATTERN = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


def parse_srt(srt_path: Path) -> list[TelemetryFrame]:
    """Parse a drone SRT telemetry file into structured data."""
    text = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = text.strip().split("\n\n")
    frames = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        try:
            index = int(lines[0].strip())
        except ValueError:
            continue

        # Parse timestamps
        ts_match = _TIMESTAMP_PATTERN.search(lines[1])
        if not ts_match:
            continue

        start_ts = ts_match.group(1)
        end_ts = ts_match.group(2)
        seconds = _timestamp_to_seconds(start_ts)

        # Parse telemetry data
        telemetry_line = " ".join(lines[2:])
        # Strip ASS/SSA formatting tags
        telemetry_line = re.sub(r"\{[^}]*\}", "", telemetry_line).strip()

        frame = TelemetryFrame(
            index=index,
            timestamp_start=start_ts,
            timestamp_end=end_ts,
            seconds=seconds,
        )

        tm = _TELEMETRY_PATTERN.search(telemetry_line)
        if tm:
            frame.f_stop = float(tm.group(1))
            frame.shutter_speed = tm.group(2)
            frame.iso = int(tm.group(3))
            frame.ev = float(tm.group(4))
            frame.height_m = float(tm.group(5))
            frame.distance_m = float(tm.group(6))
            frame.horizontal_speed_ms = float(tm.group(7))
            frame.descent_speed_ms = float(tm.group(8))
            frame.longitude = float(tm.group(9))
            frame.latitude = float(tm.group(10))
            frame.zoom = float(tm.group(11))

        frames.append(frame)

    return frames


def export_json(frames: list[TelemetryFrame], output: Path) -> None:
    """Export telemetry frames to JSON."""
    data = [asdict(f) for f in frames]
    output.write_text(json.dumps(data, indent=2))


def export_csv(frames: list[TelemetryFrame], output: Path) -> None:
    """Export telemetry frames to CSV."""
    if not frames:
        return

    fieldnames = list(asdict(frames[0]).keys())
    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for frame in frames:
            writer.writerow(asdict(frame))


def export_gpx(frames: list[TelemetryFrame], output: Path, name: str = "Flight Track") -> None:
    """Export GPS track as GPX file for mapping tools."""
    points = [f for f in frames if f.gps is not None and f.latitude != 0.0]
    if not points:
        return

    gpx_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="skyforge" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <trk><name>{name}</name><trkseg>",
    ]

    for p in points:
        ele = f"<ele>{p.height_m}</ele>" if p.height_m is not None else ""
        gpx_lines.append(f'    <trkpt lat="{p.latitude}" lon="{p.longitude}">{ele}</trkpt>')

    gpx_lines.extend(["  </trkseg></trk>", "</gpx>"])
    output.write_text("\n".join(gpx_lines))


def export_kml(frames: list[TelemetryFrame], output: Path, name: str = "Flight Track") -> None:
    """Export GPS track as KML file for Google Earth."""
    points = [f for f in frames if f.gps is not None and f.latitude != 0.0]
    if not points:
        return

    coords = "\n".join(f"        {p.longitude},{p.latitude},{p.height_m or 0}" for p in points)

    takeoff = f"{points[0].longitude},{points[0].latitude},{points[0].height_m or 0}"
    landing = f"{points[-1].longitude},{points[-1].latitude},{points[-1].height_m or 0}"

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    <Style id="flightPath">
      <LineStyle>
        <color>ff0000ff</color>
        <width>3</width>
      </LineStyle>
    </Style>
    <Placemark>
      <name>{name}</name>
      <styleUrl>#flightPath</styleUrl>
      <LineString>
        <altitudeMode>relativeToGround</altitudeMode>
        <coordinates>
{coords}
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Takeoff</name>
      <Point>
        <coordinates>{takeoff}</coordinates>
      </Point>
    </Placemark>
    <Placemark>
      <name>Landing</name>
      <Point>
        <coordinates>{landing}</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""
    output.write_text(kml)


def summary(frames: list[TelemetryFrame]) -> dict:
    """Generate a summary of telemetry data."""
    if not frames:
        return {}

    gps_frames = [f for f in frames if f.gps is not None]
    heights = [f.height_m for f in frames if f.height_m is not None]
    speeds = [f.horizontal_speed_ms for f in frames if f.horizontal_speed_ms is not None]
    distances = [f.distance_m for f in frames if f.distance_m is not None]

    return {
        "total_frames": len(frames),
        "duration_s": frames[-1].seconds if frames else 0,
        "gps_points": len(gps_frames),
        "start_gps": gps_frames[0].gps if gps_frames else None,
        "end_gps": gps_frames[-1].gps if gps_frames else None,
        "max_height_m": max(heights) if heights else None,
        "max_height_ft": max(heights) * 3.28084 if heights else None,
        "max_speed_ms": max(speeds) if speeds else None,
        "max_speed_mph": max(speeds) * 2.23694 if speeds else None,
        "max_distance_m": max(distances) if distances else None,
        "iso_range": (
            f"{min(f.iso for f in frames if f.iso)}-{max(f.iso for f in frames if f.iso)}"
            if any(f.iso for f in frames)
            else None
        ),
    }


def _timestamp_to_seconds(ts: str) -> float:
    """Convert SRT timestamp 'HH:MM:SS,mmm' to seconds."""
    parts = ts.replace(",", ".").split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

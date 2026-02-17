"""Geo utilities — distance, stats, GeoJSON, and interactive map generation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from skyforge.core.telemetry import TelemetryFrame

_EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class GeoStats:
    """Aggregated geographic statistics for a drone flight."""

    total_distance_m: float
    max_altitude_m: float
    min_altitude_m: float
    avg_speed_ms: float
    max_speed_ms: float
    duration_s: float
    start_coords: tuple[float, float]
    end_coords: tuple[float, float]
    bbox: tuple[float, float, float, float]  # min_lat, min_lon, max_lat, max_lon


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance in meters between two GPS points.

    Uses the Haversine formula for accuracy on Earth-scale distances.

    Args:
        lat1: Latitude of the first point in degrees.
        lon1: Longitude of the first point in degrees.
        lat2: Latitude of the second point in degrees.
        lon2: Longitude of the second point in degrees.

    Returns:
        Distance in meters.
    """
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_M * c


def calculate_stats(frames: list[TelemetryFrame]) -> GeoStats:
    """Compute geographic statistics from telemetry frames.

    Filters to frames that have GPS data, then calculates cumulative distance
    via haversine, altitude extremes, speed stats, and bounding box.

    Args:
        frames: List of telemetry frames (may include frames without GPS).

    Returns:
        GeoStats with computed values.

    Raises:
        ValueError: If no frames contain GPS data.
    """
    gps_frames = [f for f in frames if f.gps is not None]
    if not gps_frames:
        raise ValueError("No frames with GPS data available for stats calculation.")

    # Cumulative track distance
    total_distance = 0.0
    for i in range(1, len(gps_frames)):
        prev = gps_frames[i - 1]
        curr = gps_frames[i]
        total_distance += haversine_distance(
            prev.latitude,
            prev.longitude,  # type: ignore[arg-type]
            curr.latitude,
            curr.longitude,  # type: ignore[arg-type]
        )

    # Altitude stats (from all frames, not just GPS frames)
    altitudes = [f.height_m for f in frames if f.height_m is not None]
    max_alt = max(altitudes) if altitudes else 0.0
    min_alt = min(altitudes) if altitudes else 0.0

    # Speed stats
    speeds = [f.horizontal_speed_ms for f in frames if f.horizontal_speed_ms is not None]
    max_speed = max(speeds) if speeds else 0.0
    avg_speed = sum(speeds) / len(speeds) if speeds else 0.0

    # Duration
    duration = frames[-1].seconds - frames[0].seconds if len(frames) > 1 else 0.0

    # Bounding box
    lats = [f.latitude for f in gps_frames if f.latitude is not None]
    lons = [f.longitude for f in gps_frames if f.longitude is not None]
    bbox = (min(lats), min(lons), max(lats), max(lons))

    start = gps_frames[0].gps
    end = gps_frames[-1].gps

    return GeoStats(
        total_distance_m=total_distance,
        max_altitude_m=max_alt,
        min_altitude_m=min_alt,
        avg_speed_ms=avg_speed,
        max_speed_ms=max_speed,
        duration_s=duration,
        start_coords=start,  # type: ignore[arg-type]
        end_coords=end,  # type: ignore[arg-type]
        bbox=bbox,
    )


def to_geojson(
    frames: list[TelemetryFrame],
    properties: dict | None = None,
) -> dict:
    """Build a GeoJSON FeatureCollection from telemetry frames.

    Produces a LineString for the flight track plus Point features for the
    start and end positions.

    Args:
        frames: Telemetry frames with GPS data.
        properties: Optional properties dict to attach to the LineString feature.

    Returns:
        GeoJSON FeatureCollection dict.
    """
    gps_frames = [f for f in frames if f.gps is not None]

    # LineString coordinates: [lon, lat, altitude]
    coords = []
    for f in gps_frames:
        alt = f.height_m if f.height_m is not None else 0.0
        coords.append([f.longitude, f.latitude, alt])

    features: list[dict] = []

    if coords:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords,
                },
                "properties": properties or {"name": "Flight Track"},
            }
        )

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": coords[0],
                },
                "properties": {"name": "Start", "marker-color": "#00ff00"},
            }
        )

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": coords[-1],
                },
                "properties": {"name": "End", "marker-color": "#ff0000"},
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def generate_map_html(
    frames: list[TelemetryFrame],
    output: Path,
    stats: GeoStats | None = None,
) -> Path:
    """Write a self-contained HTML file with an interactive Leaflet.js flight map.

    Features:
        - Flight track as a blue polyline
        - Altitude color gradient overlay (green=low, red=high) when altitude data exists
        - Green start marker and red end marker with stat popups
        - Auto-fit to track bounds

    Args:
        frames: Telemetry frames with GPS data.
        output: Path where the HTML file will be written.
        stats: Pre-computed GeoStats; computed automatically if None.

    Returns:
        The output Path that was written.

    Raises:
        ValueError: If no frames contain GPS data.
    """
    gps_frames = [f for f in frames if f.gps is not None]
    if not gps_frames:
        raise ValueError("No GPS data available for map generation.")

    if stats is None:
        stats = calculate_stats(frames)

    # Build coordinate arrays for JS
    track_coords = [
        [f.latitude, f.longitude]  # Leaflet uses [lat, lng]
        for f in gps_frames
    ]
    altitudes = [f.height_m if f.height_m is not None else 0.0 for f in gps_frames]

    track_json = json.dumps(track_coords)
    alt_json = json.dumps(altitudes)

    # Stats for popup
    distance_str = (
        f"{stats.total_distance_m:.0f}m"
        if stats.total_distance_m < 1000
        else f"{stats.total_distance_m / 1000:.2f}km"
    )
    duration_min = stats.duration_s / 60
    speed_mph = stats.max_speed_ms * 2.23694
    alt_ft = stats.max_altitude_m * 3.28084

    start_popup = (
        f"<b>Start</b><br>Coords: {stats.start_coords[0]:.6f}, {stats.start_coords[1]:.6f}"
    )
    end_popup = (
        f"<b>End</b><br>"
        f"Coords: {stats.end_coords[0]:.6f}, {stats.end_coords[1]:.6f}<br>"
        f"Distance: {distance_str}<br>"
        f"Duration: {duration_min:.1f} min<br>"
        f"Max Alt: {stats.max_altitude_m:.1f}m ({alt_ft:.0f}ft)<br>"
        f"Max Speed: {stats.max_speed_ms:.1f}m/s ({speed_mph:.1f}mph)"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Flight Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
      crossorigin="">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  #map {{ width: 100vw; height: 100vh; }}
  .info-panel {{
    position: absolute; top: 10px; right: 10px; z-index: 1000;
    background: rgba(255,255,255,0.92); border-radius: 8px;
    padding: 12px 16px; font-size: 13px; line-height: 1.6;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15); max-width: 260px;
  }}
  .info-panel h3 {{ margin-bottom: 6px; font-size: 14px; }}
  .info-panel .label {{ color: #666; }}
  .info-panel .value {{ font-weight: 600; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="info-panel">
  <h3>Flight Stats</h3>
  <span class="label">Distance:</span> <span class="value">{distance_str}</span><br>
  <span class="label">Duration:</span> <span class="value">{duration_min:.1f} min</span><br>
  <span class="label">Max Alt:</span>
    <span class="value">{stats.max_altitude_m:.1f}m ({alt_ft:.0f}ft)</span><br>
  <span class="label">Max Speed:</span>
    <span class="value">{stats.max_speed_ms:.1f}m/s ({speed_mph:.1f}mph)</span><br>
  <span class="label">Avg Speed:</span>
    <span class="value">{stats.avg_speed_ms:.1f}m/s</span>
</div>
<script>
(function() {{
  var coords = {track_json};
  var alts = {alt_json};

  var map = L.map('map');
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19
  }}).addTo(map);

  if (coords.length === 0) return;

  // Main track polyline (blue)
  L.polyline(coords, {{color: '#2563eb', weight: 3, opacity: 0.8}}).addTo(map);

  // Altitude gradient overlay
  var minAlt = Math.min.apply(null, alts);
  var maxAlt = Math.max.apply(null, alts);
  var altRange = maxAlt - minAlt;

  if (altRange > 0.5) {{
    for (var i = 1; i < coords.length; i++) {{
      var t = (alts[i] - minAlt) / altRange;
      var r = Math.round(255 * t);
      var g = Math.round(255 * (1 - t));
      var color = 'rgb(' + r + ',' + g + ',0)';
      L.polyline([coords[i-1], coords[i]], {{
        color: color, weight: 5, opacity: 0.6
      }}).addTo(map);
    }}
  }}

  // Start marker (green)
  L.circleMarker(coords[0], {{
    radius: 8, fillColor: '#16a34a', color: '#fff',
    weight: 2, fillOpacity: 0.9
  }}).addTo(map).bindPopup('{start_popup}');

  // End marker (red)
  L.circleMarker(coords[coords.length - 1], {{
    radius: 8, fillColor: '#dc2626', color: '#fff',
    weight: 2, fillOpacity: 0.9
  }}).addTo(map).bindPopup('{end_popup}');

  // Fit bounds with padding
  map.fitBounds(L.latLngBounds(coords), {{padding: [40, 40]}});
}})();
</script>
</body>
</html>"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    return output

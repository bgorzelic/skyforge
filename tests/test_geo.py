"""Tests for skyforge.core.geo — haversine distance, flight stats, and GeoJSON export.

These tests are pure computation: no ffmpeg/ffprobe, no media files, no network
access. Telemetry is hand-built rather than parsed, and the only file written is
a small GeoJSON dump under pytest's ``tmp_path``.

Reference great-circle distances below come from the WGS84 geodesic solution
(geopy ``geodesic``, Karney's method). ``haversine_distance`` deliberately models
Earth as a sphere of radius 6 371 000 m, which tracks the ellipsoidal solution
to within 0.44% on every pair used here, so they are compared at 1% relative
tolerance — tight enough to fail on a real formula error (see
``test_city_pairs_tolerance_would_catch_regressions``), loose enough to allow for
the spherical approximation.
"""

from __future__ import annotations

import dataclasses
import json
import math

import pytest

from skyforge.core.geo import GeoStats, calculate_stats, haversine_distance, to_geojson
from skyforge.core.telemetry import TelemetryFrame

# Mirrors the sphere radius the implementation assumes. Pinned here so the
# analytic checks below document the model rather than just re-reading it.
EARTH_RADIUS_M = 6_371_000.0

# Relative tolerance used when comparing the spherical model to geodesic truth.
CITY_RTOL = 0.01

# (name, lat1, lon1, lat2, lon2, reference_meters_geodesic)
CITY_PAIRS = [
    ("london_paris", 51.5074, -0.1278, 48.8566, 2.3522, 343_923.120),
    ("new_york_los_angeles", 40.7128, -74.0060, 34.0522, -118.2437, 3_944_422.231),
    ("tokyo_sydney", 35.6762, 139.6503, -33.8688, 151.2093, 7_792_174.827),
    ("london_cape_town", 51.5074, -0.1278, -33.9249, 18.4241, 9_636_428.841),
    ("sydney_auckland", -33.8688, 151.2093, -36.8485, 174.7633, 2_160_508.809),
    ("new_york_london", 40.7128, -74.0060, 51.5074, -0.1278, 5_585_233.579),
    ("sao_paulo_cape_town", -23.5505, -46.6333, -33.9249, 18.4241, 6_355_601.721),
]


def make_frame(
    index: int,
    seconds: float,
    latitude: float | None = None,
    longitude: float | None = None,
    height_m: float | None = None,
    speed_ms: float | None = None,
) -> TelemetryFrame:
    """Build a TelemetryFrame by hand so no SRT parsing is involved."""
    return TelemetryFrame(
        index=index,
        timestamp_start=f"00:00:{int(seconds):02d},000",
        timestamp_end=f"00:00:{int(seconds) + 1:02d},000",
        seconds=seconds,
        height_m=height_m,
        horizontal_speed_ms=speed_ms,
        latitude=latitude,
        longitude=longitude,
    )


@pytest.fixture
def seattle_track() -> list[TelemetryFrame]:
    """Four frames near Seattle: one climb, a lat/lon jog, then a dwell.

    The final frame has no speed reading so stats must cope with partial data.
    """
    return [
        make_frame(0, 0.0, 47.6068, -122.3321, 0.0, 0.0),
        make_frame(1, 10.0, 47.6078, -122.3321, 30.0, 5.0),
        make_frame(2, 20.0, 47.6068, -122.3311, 60.0, 15.0),
        make_frame(3, 30.0, 47.6068, -122.3311, 45.0, None),
    ]


@pytest.fixture
def mixed_sign_track() -> list[TelemetryFrame]:
    """A track spanning both hemispheres, so bbox field order is unambiguous."""
    return [
        make_frame(0, 0.0, -33.8688, 151.2093, 10.0, 4.0),  # Sydney
        make_frame(1, 5.0, 35.6762, 139.6503, 20.0, 12.0),  # Tokyo
        make_frame(2, 10.0, -36.8485, 174.7633, 5.0, 30.0),  # Auckland
    ]


class TestHaversineDistance:
    """Great-circle distance between GPS points."""

    @pytest.mark.parametrize(
        ("name", "lat1", "lon1", "lat2", "lon2", "expected_m"),
        CITY_PAIRS,
        ids=[pair[0] for pair in CITY_PAIRS],
    )
    def test_known_city_pairs(
        self,
        name: str,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        expected_m: float,
    ) -> None:
        result = haversine_distance(lat1, lon1, lat2, lon2)
        assert result == pytest.approx(expected_m, rel=CITY_RTOL), name

    def test_city_pairs_tolerance_would_catch_regressions(self) -> None:
        """Guard the guard: a plausible formula bug must blow past 1% tolerance.

        Each mutant below differs from the implementation only in the way a real
        regression would — a missing cosine latitude correction, a dropped factor
        of two, a latitude/longitude argument swap. A mutant that no pair can see
        would mean the tolerance above is slack rather than meaningful, so this
        asserts each one is caught by a majority of the city pairs.
        """
        mutants = {
            "dropped_cos": self._dropped_cosine_correction,
            "missing_factor_of_two": self._missing_factor_of_two,
            "swapped_lat_lon": self._swapped_lat_lon,
        }
        for label, mutant in mutants.items():
            errors = []
            for _name, lat1, lon1, lat2, lon2, _ref in CITY_PAIRS:
                base = haversine_distance(lat1, lon1, lat2, lon2)
                errors.append(abs(mutant(lat1, lon1, lat2, lon2) - base) / base)
            caught = sum(1 for err in errors if err > CITY_RTOL)
            assert caught > len(errors) / 2, f"{label} slipped through: {errors}"

    @staticmethod
    def _dropped_cosine_correction(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Mutant: the cos(lat1) * cos(lat2) term is omitted."""
        lat1r, lon1r, lat2r, lon2r = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
        dlat, dlon = lat2r - lat1r, lon2r - lon1r
        a = math.sin(dlat / 2) ** 2 + math.sin(dlon / 2) ** 2
        return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _missing_factor_of_two(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Mutant: the central angle factor of two is dropped."""
        lat1r, lon1r, lat2r, lon2r = (math.radians(x) for x in (lat1, lon1, lat2, lon2))
        dlat, dlon = lat2r - lat1r, lon2r - lon1r
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
        return EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _swapped_lat_lon(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Mutant: latitude and longitude arguments are transposed."""
        return haversine_distance(lon1, lat1, lon2, lat2)

    def test_zero_distance_for_identical_point(self) -> None:
        assert haversine_distance(37.7749, -122.4194, 37.7749, -122.4194) == 0.0

    def test_zero_distance_at_origin(self) -> None:
        assert haversine_distance(0.0, 0.0, 0.0, 0.0) == 0.0

    def test_zero_distance_for_repeated_high_latitude_point(self) -> None:
        """Repeating one far-from-origin fix keeps the distance at zero."""
        assert haversine_distance(-33.8688, 151.2093, -33.8688, 151.2093) == 0.0

    def test_zero_distance_across_longitudes_at_north_pole(self) -> None:
        """All longitudes meet at a pole, so the gap must vanish."""
        assert haversine_distance(90.0, 0.0, 90.0, 180.0) == pytest.approx(0.0, abs=1e-6)

    def test_distance_is_symmetric(self) -> None:
        forward = haversine_distance(51.5074, -0.1278, -33.9249, 18.4241)
        backward = haversine_distance(-33.9249, 18.4241, 51.5074, -0.1278)
        assert forward == pytest.approx(backward, rel=1e-12)

    def test_returns_float(self) -> None:
        assert isinstance(haversine_distance(51.5074, -0.1278, 48.8566, 2.3522), float)

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [(0.0, 0.0), (0.0, 10.0), (30.0, -170.0), (-30.0, 170.0), (60.0, 0.0), (-45.0, -122.0)],
    )
    def test_one_degree_of_latitude_at_equator(self, lat: float, lon: float) -> None:
        """Along a meridian a degree of latitude is a fixed arc, at any longitude."""
        expected = EARTH_RADIUS_M * math.pi / 180
        assert haversine_distance(lat, lon, lat + 1.0, lon) == pytest.approx(expected, rel=1e-12)

    def test_one_degree_of_longitude_at_equator(self) -> None:
        expected = EARTH_RADIUS_M * math.pi / 180
        assert haversine_distance(0.0, 0.0, 0.0, 1.0) == pytest.approx(expected, rel=1e-12)

    @pytest.mark.parametrize("latitude", [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 89.0])
    def test_one_degree_of_longitude_shrinks_towards_the_pole(self, latitude: float) -> None:
        """Longitude degrees narrow by cos(latitude); latitude degrees do not.

        cos(latitude) * arc is the small-angle limit, so it is matched loosely
        here — the deviation is a genuine property of the geometry, not slack in
        the implementation (it stays under 1.3e-5 relative even at 89 degrees).
        """
        expected = (EARTH_RADIUS_M * math.pi / 180) * math.cos(math.radians(latitude))
        assert haversine_distance(latitude, 0.0, latitude, 1.0) == pytest.approx(expected, rel=1e-4)

    def test_longitude_span_narrows_monotonically_with_latitude(self) -> None:
        spans = [haversine_distance(lat, 0.0, lat, 1.0) for lat in (0.0, 30.0, 60.0, 89.0)]
        assert spans == sorted(spans, reverse=True)

    def test_antimeridian_crossing_takes_the_short_path(self) -> None:
        """A degree either side of the date line is one degree apart, not 359."""
        expected = EARTH_RADIUS_M * math.pi / 180
        result = haversine_distance(0.0, 179.5, 0.0, -179.5)
        assert result == pytest.approx(expected, rel=1e-12)
        assert result < EARTH_RADIUS_M * math.pi / 4  # not the long way round

    def test_antimeridian_half_degree_apart(self) -> None:
        expected = EARTH_RADIUS_M * math.pi / 180 / 2
        assert haversine_distance(0.0, 179.75, 0.0, -179.75) == pytest.approx(expected, rel=1e-12)

    def test_quarter_meridian_from_pole_to_equator(self) -> None:
        expected = EARTH_RADIUS_M * math.pi / 2
        assert haversine_distance(90.0, 0.0, 0.0, 0.0) == pytest.approx(expected, rel=1e-12)

    def test_antipodal_equator_points_span_half_the_circumference(self) -> None:
        expected = EARTH_RADIUS_M * math.pi
        assert haversine_distance(0.0, 0.0, 0.0, 180.0) == pytest.approx(expected, rel=1e-12)

    def test_symmetric_points_about_the_equator(self) -> None:
        """Two degrees of latitude straddling the equator span 2 degrees of arc."""
        expected = EARTH_RADIUS_M * math.pi / 180 * 2
        assert haversine_distance(1.0, 0.0, -1.0, 0.0) == pytest.approx(expected, rel=1e-12)

    def test_south_and_north_symmetry(self) -> None:
        """The model is mirror-symmetric about the equator."""
        assert haversine_distance(-45.0, 10.0, 45.0, 10.0) == pytest.approx(
            haversine_distance(45.0, 10.0, -45.0, 10.0), rel=1e-12
        )


class TestCalculateStats:
    """Aggregated flight statistics from telemetry frames."""

    def test_total_distance_sums_consecutive_gps_legs(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        legs = [
            haversine_distance(47.6068, -122.3321, 47.6078, -122.3321),
            haversine_distance(47.6078, -122.3321, 47.6068, -122.3311),
            haversine_distance(47.6068, -122.3311, 47.6068, -122.3311),
        ]
        assert legs[2] == 0.0  # the track dwells on its final fix
        assert calculate_stats(seattle_track).total_distance_m == pytest.approx(
            sum(legs), rel=1e-12
        )

    def test_total_distance_is_zero_for_stationary_track(self) -> None:
        frames = [
            make_frame(0, 0.0, 47.6068, -122.3321, 10.0, 0.0),
            make_frame(1, 1.0, 47.6068, -122.3321, 10.0, 0.0),
            make_frame(2, 2.0, 47.6068, -122.3321, 10.0, 0.0),
        ]
        assert calculate_stats(frames).total_distance_m == 0.0

    def test_altitude_extremes_cover_every_frame(self, seattle_track: list[TelemetryFrame]) -> None:
        stats = calculate_stats(seattle_track)
        assert stats.max_altitude_m == 60.0
        assert stats.min_altitude_m == 0.0

    def test_altitude_extremes_include_frames_without_gps(self) -> None:
        """Altitude is read from all frames, not just the ones carrying a fix."""
        frames = [
            make_frame(0, 0.0, 47.6068, -122.3321, 12.0, 1.0),
            make_frame(1, 1.0, None, None, 88.0, 2.0),
        ]
        stats = calculate_stats(frames)
        assert stats.max_altitude_m == 88.0
        assert stats.min_altitude_m == 12.0

    def test_altitude_defaults_to_zero_when_absent(self) -> None:
        frames = [make_frame(0, 0.0, 47.6068, -122.3321), make_frame(1, 1.0, 47.6, -122.33)]
        stats = calculate_stats(frames)
        assert stats.max_altitude_m == 0.0
        assert stats.min_altitude_m == 0.0

    def test_speed_stats_ignore_missing_readings(self, seattle_track: list[TelemetryFrame]) -> None:
        """Only the three frames reporting speed contribute (0, 5, 15 m/s)."""
        stats = calculate_stats(seattle_track)
        assert stats.max_speed_ms == 15.0
        assert stats.avg_speed_ms == pytest.approx(20.0 / 3.0, rel=1e-12)

    def test_speed_stats_default_to_zero_when_absent(self) -> None:
        frames = [make_frame(0, 0.0, 47.6068, -122.3321, 10.0), make_frame(1, 1.0, 47.6, -122.3)]
        stats = calculate_stats(frames)
        assert stats.max_speed_ms == 0.0
        assert stats.avg_speed_ms == 0.0

    def test_duration_spans_first_to_last_frame(self, seattle_track: list[TelemetryFrame]) -> None:
        assert calculate_stats(seattle_track).duration_s == 30.0

    def test_duration_includes_frames_without_gps(self) -> None:
        frames = [
            make_frame(0, 0.0, 47.6068, -122.3321, 10.0, 1.0),
            make_frame(1, 20.0, 47.6078, -122.3321, 20.0, 2.0),
            make_frame(2, 45.0, None, None, 20.0, 3.0),
        ]
        assert calculate_stats(frames).duration_s == 45.0

    def test_duration_is_zero_for_a_single_frame(self) -> None:
        frames = [make_frame(0, 12.0, 47.6, -122.3, 5.0, 2.0)]
        assert calculate_stats(frames).duration_s == 0.0

    def test_start_and_end_coords_are_latitude_then_longitude(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        """GeoStats reports (lat, lon) — the opposite order from GeoJSON output."""
        stats = calculate_stats(seattle_track)
        assert stats.start_coords == (47.6068, -122.3321)
        assert stats.end_coords == (47.6068, -122.3311)
        assert stats.start_coords[0] == seattle_track[0].latitude
        assert stats.start_coords[1] == seattle_track[0].longitude

    def test_start_and_end_coords_skip_frames_without_gps(self) -> None:
        """The endpoints are the first and last *fixes*, not the first and last frames.

        Dropouts sit at both ends of the list here, so reading the endpoints off
        the unfiltered frame list would yield None instead of a coordinate.
        """
        frames = [
            make_frame(0, 0.0, None, None, 10.0, 1.0),
            make_frame(1, 5.0, 47.6068, -122.3321, 10.0, 1.0),
            make_frame(2, 10.0, 47.6078, -122.3321, 10.0, 1.0),
            make_frame(3, 15.0, None, None, 10.0, 1.0),
        ]
        stats = calculate_stats(frames)
        assert stats.start_coords == (47.6068, -122.3321)
        assert stats.end_coords == (47.6078, -122.3321)

    def test_bbox_is_min_lat_min_lon_max_lat_max_lon(
        self, mixed_sign_track: list[TelemetryFrame]
    ) -> None:
        stats = calculate_stats(mixed_sign_track)
        assert stats.bbox == (-36.8485, 139.6503, 35.6762, 174.7633)

    def test_bbox_of_a_single_point_degenerates(self) -> None:
        frames = [make_frame(0, 0.0, -33.8688, 151.2093, 10.0, 1.0)]
        assert calculate_stats(frames).bbox == (-33.8688, 151.2093, -33.8688, 151.2093)

    def test_gps_gap_is_bridged_by_a_direct_leg(self) -> None:
        """Distance joins consecutive fixes, so a dropout is measured as one long leg."""
        with_dropout = [
            make_frame(0, 0.0, 47.6068, -122.3321, 10.0, 1.0),
            make_frame(1, 1.0, None, None, 10.0, 1.0),
            make_frame(2, 2.0, 47.6068, -122.3311, 10.0, 1.0),
        ]
        expected = haversine_distance(47.6068, -122.3321, 47.6068, -122.3311)
        assert calculate_stats(with_dropout).total_distance_m == pytest.approx(expected, rel=1e-12)

    def test_raises_when_no_frame_has_gps(self) -> None:
        frames = [make_frame(0, 0.0, None, None, 10.0, 1.0), make_frame(1, 1.0, None, None)]
        with pytest.raises(ValueError, match="No frames with GPS data"):
            calculate_stats(frames)

    def test_raises_on_empty_frame_list(self) -> None:
        with pytest.raises(ValueError, match="No frames with GPS data"):
            calculate_stats([])

    def test_raises_when_only_one_coordinate_is_present(self) -> None:
        """A fix needs both coordinates; half a fix is not a fix."""
        frames = [make_frame(0, 0.0, 47.6068, None, 10.0, 1.0)]
        with pytest.raises(ValueError, match="No frames with GPS data"):
            calculate_stats(frames)

    def test_returns_geo_stats_dataclass(self, seattle_track: list[TelemetryFrame]) -> None:
        assert isinstance(calculate_stats(seattle_track), GeoStats)

    def test_geo_stats_is_immutable(self, seattle_track: list[TelemetryFrame]) -> None:
        stats = calculate_stats(seattle_track)
        with pytest.raises(dataclasses.FrozenInstanceError):
            stats.total_distance_m = 1.0  # type: ignore[misc]


class TestToGeoJson:
    """GeoJSON FeatureCollection structure."""

    def test_top_level_envelope(self, seattle_track: list[TelemetryFrame]) -> None:
        collection = to_geojson(seattle_track)
        assert collection["type"] == "FeatureCollection"
        assert set(collection) == {"type", "features"}
        assert isinstance(collection["features"], list)

    def test_emits_track_start_and_end_features(self, seattle_track: list[TelemetryFrame]) -> None:
        features = to_geojson(seattle_track)["features"]
        assert len(features) == 3
        assert [f["geometry"]["type"] for f in features] == ["LineString", "Point", "Point"]
        assert [f["properties"]["name"] for f in features] == [
            "Flight Track",
            "Start",
            "End",
        ]
        for feature in features:
            assert feature["type"] == "Feature"
            assert set(feature) == {"type", "geometry", "properties"}

    def test_linestring_coordinates_are_longitude_latitude_altitude(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        """GeoJSON positions are [lon, lat, alt] — the reverse of GeoStats coords."""
        line = to_geojson(seattle_track)["features"][0]
        assert line["geometry"]["coordinates"] == [
            [-122.3321, 47.6068, 0.0],
            [-122.3321, 47.6078, 30.0],
            [-122.3311, 47.6068, 60.0],
            [-122.3311, 47.6068, 45.0],
        ]

    def test_coordinates_respect_geojson_axis_ranges(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        """A swapped lat/lon would push latitude outside the valid range."""
        for lon, lat, _alt in to_geojson(seattle_track)["features"][0]["geometry"]["coordinates"]:
            assert -180.0 <= lon <= 180.0
            assert -90.0 <= lat <= 90.0
            assert 47.0 <= lat <= 48.0  # the Seattle track's true latitude band

    def test_linestring_preserves_frame_order(self, mixed_sign_track: list[TelemetryFrame]) -> None:
        line = to_geojson(mixed_sign_track)["features"][0]
        assert [(pos[0], pos[1]) for pos in line["geometry"]["coordinates"]] == [
            (151.2093, -33.8688),
            (139.6503, 35.6762),
            (174.7633, -36.8485),
        ]

    def test_start_and_end_points_reuse_track_vertices(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        features = to_geojson(seattle_track)["features"]
        vertices = features[0]["geometry"]["coordinates"]
        assert features[1]["geometry"]["coordinates"] == vertices[0]
        assert features[2]["geometry"]["coordinates"] == vertices[-1]

    def test_start_and_end_marker_colors(self, seattle_track: list[TelemetryFrame]) -> None:
        features = to_geojson(seattle_track)["features"]
        assert features[1]["properties"]["marker-color"] == "#00ff00"
        assert features[2]["properties"]["marker-color"] == "#ff0000"

    def test_default_track_properties(self, seattle_track: list[TelemetryFrame]) -> None:
        line = to_geojson(seattle_track)["features"][0]
        assert line["properties"] == {"name": "Flight Track"}

    def test_supplied_properties_replace_the_default(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        properties = {"name": "Site Survey", "pilot": "ops", "sortie": 7}
        line = to_geojson(seattle_track, properties)["features"][0]
        assert line["properties"] == properties
        assert "Flight Track" not in line["properties"]["name"]

    def test_empty_properties_fall_back_to_the_default(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        """An empty dict is falsy, so it does not blank out the track name."""
        line = to_geojson(seattle_track, {})["features"][0]
        assert line["properties"] == {"name": "Flight Track"}

    def test_marker_properties_are_not_overridden(
        self, seattle_track: list[TelemetryFrame]
    ) -> None:
        features = to_geojson(seattle_track, {"name": "Site Survey"})["features"]
        assert features[1]["properties"] == {"name": "Start", "marker-color": "#00ff00"}
        assert features[2]["properties"] == {"name": "End", "marker-color": "#ff0000"}

    def test_frames_without_gps_are_dropped(self) -> None:
        frames = [
            make_frame(0, 0.0, 47.6068, -122.3321, 10.0, 1.0),
            make_frame(1, 1.0, None, None, 999.0, 99.0),
            make_frame(2, 2.0, 47.6068, -122.3311, 20.0, 2.0),
        ]
        line = to_geojson(frames)["features"][0]
        assert line["geometry"]["coordinates"] == [
            [-122.3321, 47.6068, 10.0],
            [-122.3311, 47.6068, 20.0],
        ]

    def test_missing_altitude_becomes_zero(self) -> None:
        frames = [
            make_frame(0, 0.0, 47.6068, -122.3321),
            make_frame(1, 1.0, 47.6068, -122.3311, 12.5),
        ]
        line = to_geojson(frames)["features"][0]
        assert line["geometry"]["coordinates"] == [
            [-122.3321, 47.6068, 0.0],
            [-122.3311, 47.6068, 12.5],
        ]

    def test_single_fix_yields_a_one_vertex_linestring(self) -> None:
        frames = [make_frame(0, 0.0, 47.6068, -122.3321, 10.0, 1.0)]
        features = to_geojson(frames)["features"]
        assert features[0]["geometry"] == {
            "type": "LineString",
            "coordinates": [[-122.3321, 47.6068, 10.0]],
        }
        assert features[1]["geometry"]["coordinates"] == features[2]["geometry"]["coordinates"]

    def test_empty_input_yields_an_empty_collection(self) -> None:
        assert to_geojson([]) == {"type": "FeatureCollection", "features": []}

    def test_frames_without_gps_yield_an_empty_collection(self) -> None:
        frames = [make_frame(0, 0.0, None, None, 10.0, 1.0)]
        assert to_geojson(frames) == {"type": "FeatureCollection", "features": []}

    def test_collection_is_json_serializable(
        self, seattle_track: list[TelemetryFrame], tmp_path
    ) -> None:
        """The dict round-trips through a file unchanged, ready for map tooling."""
        collection = to_geojson(seattle_track)
        output = tmp_path / "track.geojson"
        output.write_text(json.dumps(collection, indent=2), encoding="utf-8")
        assert json.loads(output.read_text(encoding="utf-8")) == collection
        assert output.stat().st_size > 0

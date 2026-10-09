import json

import geopandas as gpd
from shapely.geometry import MultiPolygon, Point, box

from analysis import add_metrics, classify_quantiles
import pytest

from map_view import (
    build_deck,
    build_layers,
    elevation_scale_for,
    polygon_records,
    view_state_for,
)


def mapped_frame() -> gpd.GeoDataFrame:
    frame = gpd.GeoDataFrame(
        {
            "zcta": ["75022", "75028"],
            "total_population": [20_000.0, 30_000.0],
            "median_age": [38.0, 40.0],
            "median_household_income": [120_000.0, 140_000.0],
            "under_5": [1_000.0, 1_500.0],
            "ages_5_9": [1_100.0, 1_600.0],
            "ages_10_14": [1_200.0, 1_700.0],
            "total_households": [8_000.0, 10_000.0],
            "total_family_households": [5_000.0, 7_000.0],
            "income_100_149": [15.0, 16.0],
            "income_150_199": [10.0, 12.0],
            "income_200_plus": [8.0, 10.0],
        },
        geometry=[
            box(-97.1, 32.9, -97.0, 33.0),
            box(-97.0, 33.0, -96.9, 33.1),
        ],
        crs="EPSG:4326",
    )
    frame = add_metrics(frame)
    return classify_quantiles(frame, "target_kids")[0]


def poi_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["Oak School", "Kids Health", "Quick Cuts"],
            "category": ["school", "pediatrician", "hairdresser"],
            "latitude": [33.0, 33.01, 33.02],
            "longitude": [-97.0, -97.01, -97.02],
        },
        geometry=[Point(-97.0, 33.0), Point(-97.01, 33.01), Point(-97.02, 33.02)],
        crs="EPSG:4326",
    )


def test_polygon_records_explode_multipolygons_and_add_tooltips():
    multi = MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)])
    frame = mapped_frame().iloc[[0]].copy()
    frame.geometry = [multi]

    records = polygon_records(frame)

    assert len(records) == 2
    assert all(record["tooltip_title"] == "ZCTA 75022" for record in records)
    assert all(record["polygon"] for record in records)


def test_build_layers_creates_base_outline_and_enabled_pois():
    frame = mapped_frame()

    layers = build_layers(
        frame,
        selected=frame.iloc[[0]],
        pois=poi_frame(),
        enabled_poi_categories={"school", "hairdresser"},
    )

    assert [layer.id for layer in layers] == [
        "demographic-polygons",
        "territory-outline",
        "poi-school",
        "poi-hairdresser",
    ]
    base = json.loads(layers[0].to_json())
    assert base["@@" + "type"] == "PolygonLayer"
    assert base["extruded"] is True
    assert base["getElevation"] == "@@=target_kids"


def test_view_state_focuses_selected_territory():
    statewide = view_state_for(mapped_frame())
    selected = view_state_for(mapped_frame(), mapped_frame().iloc[[0]])

    assert statewide.zoom == 5
    assert selected.zoom > statewide.zoom
    assert selected.longitude == -97.05
    assert selected.latitude == 32.95


def test_build_deck_contains_stable_tooltip_and_layers():
    frame = mapped_frame()
    layers = build_layers(frame)

    deck = build_deck(layers, view_state_for(frame))
    payload = json.loads(deck.to_json())

    assert payload["layers"][0]["id"] == "demographic-polygons"
    assert "tooltip_title" in deck._tooltip["html"]


def test_poi_tooltip_escapes_untrusted_osm_names():
    pois = poi_frame().iloc[[0]].copy()
    pois.loc[pois.index[0], "name"] = "<img src=x onerror=alert(1)>"

    layers = build_layers(
        mapped_frame(),
        pois=pois,
        enabled_poi_categories={"school"},
    )
    payload = json.loads(layers[-1].to_json())

    assert payload["data"][0]["tooltip_title"] == (
        "&lt;img src=x onerror=alert(1)&gt;"
    )

def test_elevation_scale_uses_selected_territory_percentile():
    demographics = mapped_frame()
    selected = demographics.iloc[[0]].copy()
    selected.loc[selected.index[0], "target_kids"] = 1_000.0

    scale = elevation_scale_for(demographics, selected)

    assert scale == pytest.approx(500.0 / 1_000.0)


def test_elevation_scale_clamps_empty_or_missing_target_kids():
    empty = mapped_frame().iloc[0:0]
    missing = mapped_frame().assign(target_kids=float("nan"))

    assert elevation_scale_for(empty) == 0.05
    assert elevation_scale_for(missing) == 0.05


def test_build_layers_applies_provided_elevation_scale():
    layers = build_layers(mapped_frame(), elevation_scale=0.25)
    payload = json.loads(layers[0].to_json())

    assert payload["elevationScale"] == 0.25

def test_poi_colors_and_labels_come_from_catalog():
    from map_view import POI_COLORS, POI_LABELS

    assert POI_LABELS["hairdresser"] == "Hair salon"
    assert POI_LABELS["cafe"] == "Cafe / coffee"
    assert POI_COLORS["beauty"] == [251, 113, 133, 220]
    assert "competitor" not in POI_COLORS


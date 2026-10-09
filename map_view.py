"""Pydeck layer and view construction."""

from __future__ import annotations

from collections.abc import Iterable
import math
from typing import Any, Final

import geopandas as gpd
import pydeck as pdk
from shapely.geometry import mapping


TEXAS_VIEW: Final = {
    "latitude": 31.0,
    "longitude": -99.0,
    "zoom": 5,
    "pitch": 45,
    "bearing": 0,
}
POI_COLORS: Final = {
    "school": [52, 211, 153, 220],
    "pediatrician": [96, 165, 250, 220],
    "competitor": [244, 114, 182, 220],
}
POI_LABELS: Final = {
    "school": "School",
    "pediatrician": "Pediatrician / clinic",
    "competitor": "Competitor salon",
}


def _display_number(value: Any, *, currency: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "Not available"
    if math.isnan(numeric):
        return "Not available"
    prefix = "$" if currency else ""
    return f"{prefix}{numeric:,.0f}"


def _lists(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_lists(item) for item in value]
    if isinstance(value, list):
        return [_lists(item) for item in value]
    return value


def polygon_records(frame: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    """Convert Polygon/MultiPolygon rows into deck.gl-ready records."""
    if frame.empty:
        return []

    exploded = frame.explode(index_parts=False, ignore_index=True)
    records: list[dict[str, Any]] = []
    for _, row in exploded.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        geometry_mapping = mapping(geometry)
        if geometry_mapping["type"] != "Polygon":
            continue

        record = row.drop(labels=["geometry"]).to_dict()
        record["polygon"] = _lists(geometry_mapping["coordinates"])
        record["tooltip_title"] = f"ZCTA {record.get('zcta', 'Unknown')}"
        record["tooltip_line_1"] = (
            f"Population: {_display_number(record.get('total_population'))}"
        )
        record["tooltip_line_2"] = (
            "Median household income: "
            f"{_display_number(record.get('median_household_income'), currency=True)}"
        )
        record["tooltip_line_3"] = (
            f"Target kids (0–14): {_display_number(record.get('target_kids'))}"
        )
        records.append(record)
    return records


def _poi_records(frame: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    records = []
    for row in frame.itertuples(index=False):
        records.append(
            {
                "name": row.name,
                "category": row.category,
                "position": [float(row.longitude), float(row.latitude)],
                "tooltip_title": row.name,
                "tooltip_line_1": POI_LABELS[row.category],
                "tooltip_line_2": "",
                "tooltip_line_3": "",
            }
        )
    return records


def build_layers(
    demographics: gpd.GeoDataFrame,
    *,
    selected: gpd.GeoDataFrame | None = None,
    pois: gpd.GeoDataFrame | None = None,
    enabled_poi_categories: Iterable[str] = (),
) -> list[pdk.Layer]:
    """Build demographic, selection, and enabled POI layers."""
    layers: list[pdk.Layer] = [
        pdk.Layer(
            "PolygonLayer",
            id="demographic-polygons",
            data=polygon_records(demographics),
            get_polygon="polygon",
            get_fill_color="fill_color",
            get_line_color=[255, 255, 255, 70],
            get_elevation="target_kids",
            elevation_scale=2,
            extruded=True,
            filled=True,
            stroked=True,
            line_width_min_pixels=1,
            pickable=True,
            auto_highlight=True,
        )
    ]

    if selected is not None and not selected.empty:
        layers.append(
            pdk.Layer(
                "PolygonLayer",
                id="territory-outline",
                data=polygon_records(selected),
                get_polygon="polygon",
                get_fill_color=[0, 0, 0, 0],
                get_line_color=[34, 211, 238, 255],
                filled=False,
                stroked=True,
                line_width_min_pixels=4,
                pickable=True,
            )
        )

    if pois is not None and not pois.empty:
        enabled = set(enabled_poi_categories)
        for category in POI_COLORS:
            if category not in enabled:
                continue
            category_frame = pois.loc[pois["category"] == category]
            if category_frame.empty:
                continue
            layers.append(
                pdk.Layer(
                    "ScatterplotLayer",
                    id=f"poi-{category}",
                    data=_poi_records(category_frame),
                    get_position="position",
                    get_fill_color=POI_COLORS[category],
                    get_line_color=[255, 255, 255, 230],
                    get_radius=180,
                    radius_min_pixels=5,
                    radius_max_pixels=14,
                    stroked=True,
                    line_width_min_pixels=1,
                    pickable=True,
                )
            )
    return layers


def view_state_for(
    demographics: gpd.GeoDataFrame,
    selected: gpd.GeoDataFrame | None = None,
) -> pdk.ViewState:
    """Return a statewide view or a territory-focused view."""
    del demographics  # The statewide view is intentionally stable.
    if selected is None or selected.empty:
        return pdk.ViewState(**TEXAS_VIEW)

    west, south, east, north = selected.total_bounds
    return pdk.ViewState(
        latitude=round(float((south + north) / 2), 6),
        longitude=round(float((west + east) / 2), 6),
        zoom=9,
        pitch=45,
        bearing=0,
    )


def build_deck(
    layers: list[pdk.Layer],
    view_state: pdk.ViewState,
) -> pdk.Deck:
    """Build the final Deck with one tooltip schema shared by all layers."""
    return pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        tooltip={
            "html": (
                "<b>{tooltip_title}</b><br/>"
                "{tooltip_line_1}<br/>"
                "{tooltip_line_2}<br/>"
                "{tooltip_line_3}"
            ),
            "style": {
                "backgroundColor": "#111827",
                "color": "#f9fafb",
            },
        },
    )

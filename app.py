"""Streamlit application for evaluating Texas franchise territories."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
import os
from typing import Any, Final

import geopandas as gpd
import pandas as pd
import requests
from dotenv import load_dotenv
import streamlit as st

from analysis import (
    NAMED_TERRITORIES,
    TerritorySelectionError,
    add_metrics,
    classify_quantiles,
    filter_demographics,
    select_territory,
    territory_bounds,
)
from data import load_census_data, load_overpass_pois
from map_view import build_deck, build_layers, view_state_for


METRICS: Final[dict[str, str]] = {
    "Target Kids (0–14)": "target_kids",
    "Family Density %": "family_density_pct",
    "High Income %": "high_income_pct",
    "Median Age": "median_age",
}
DFW_BOUNDS: Final = (32.55, -97.55, 33.45, -96.45)
PoiLoader = Callable[[tuple[float, float, float, float]], gpd.GeoDataFrame]
LOGGER = logging.getLogger(__name__)


def territory_summary(frame: gpd.GeoDataFrame) -> dict[str, float]:
    """Aggregate counts and stable summary measures for a territory."""
    households = float(frame["total_households"].sum())
    families = float(frame["total_family_households"].sum())
    family_density = families / households * 100 if households else float("nan")
    return {
        "total_population": float(frame["total_population"].sum()),
        "target_kids": float(frame["target_kids"].sum()),
        "median_household_income": float(frame["median_household_income"].mean()),
        "family_density_pct": family_density,
    }


def filter_pois(
    frame: gpd.GeoDataFrame, enabled_categories: Iterable[str]
) -> gpd.GeoDataFrame:
    """Return only the POI categories enabled in the sidebar."""
    return frame.loc[frame["category"].isin(set(enabled_categories))].copy()


def _format_metric(value: Any, *, currency: bool = False, percent: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "Not available"
    if pd.isna(numeric):
        return "Not available"
    if currency:
        return f"${numeric:,.0f}"
    if percent:
        return f"{numeric:,.1f}%"
    return f"{numeric:,.0f}"


def _maximum(frame: gpd.GeoDataFrame, column: str, floor: int) -> int:
    value = pd.to_numeric(frame[column], errors="coerce").max()
    if pd.isna(value):
        return floor
    return max(floor, int(value))


def _territory_control(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    mode = st.sidebar.radio(
        "Territory search",
        ("None", "Named place", "ZCTA"),
        horizontal=True,
    )
    try:
        if mode == "Named place":
            place = st.sidebar.selectbox("North Texas place", tuple(NAMED_TERRITORIES))
            return select_territory(frame, named_place=place)
        if mode == "ZCTA":
            zcta = st.sidebar.text_input("Five-digit ZCTA", placeholder="75028")
            if not zcta:
                return None
            return select_territory(frame, zcta=zcta.strip())
    except TerritorySelectionError as exc:
        st.sidebar.warning(str(exc))
    return None


def _render_summary(selected: gpd.GeoDataFrame | None) -> None:
    if selected is None or selected.empty:
        return
    summary = territory_summary(selected)
    st.subheader("Selected territory")
    columns = st.columns(4)
    columns[0].metric("Population", _format_metric(summary["total_population"]))
    columns[1].metric("Target kids", _format_metric(summary["target_kids"]))
    columns[2].metric(
        "Average median income",
        _format_metric(summary["median_household_income"], currency=True),
    )
    columns[3].metric(
        "Family density",
        _format_metric(summary["family_density_pct"], percent=True),
    )


def render_dashboard(
    demographics: gpd.GeoDataFrame,
    *,
    poi_loader: PoiLoader = load_overpass_pois,
) -> None:
    """Render the interactive dashboard from an already normalized frame."""
    st.title("Texas Territory Demographic Heatmapper")
    st.caption(
        "2024 ACS 5-year estimates with indicative OpenStreetMap points of interest."
    )

    metric_label = st.sidebar.selectbox("Primary metric", tuple(METRICS))
    metric = METRICS[metric_label]
    minimum_income = st.sidebar.slider(
        "Minimum median household income",
        min_value=0,
        max_value=_maximum(demographics, "median_household_income", 1_000),
        value=0,
        step=1_000,
        format="$%d",
    )
    minimum_kids = st.sidebar.slider(
        "Minimum Target Kids (0–14)",
        min_value=0,
        max_value=_maximum(demographics, "target_kids", 100),
        value=0,
        step=100,
    )
    selected = _territory_control(demographics)

    st.sidebar.subheader("OpenStreetMap overlays")
    overlay_choices = {
        "school": st.sidebar.checkbox("Schools"),
        "pediatrician": st.sidebar.checkbox("Pediatricians / clinics"),
        "competitor": st.sidebar.checkbox("Competitor salons"),
    }
    enabled_categories = {
        category for category, enabled in overlay_choices.items() if enabled
    }
    st.sidebar.caption(
        "Color shows metric quantiles. Polygon height always shows Target Kids."
    )

    filtered = filter_demographics(
        demographics,
        minimum_income=minimum_income,
        minimum_target_kids=minimum_kids,
    )
    bins: list[float] = []
    if filtered.empty:
        st.warning("No ZCTAs meet the active demographic filters.")
        classified = filtered.copy()
    else:
        try:
            classified, bins = classify_quantiles(filtered, metric)
        except ValueError:
            st.warning(
                f"{metric_label} has no usable values; polygons are shown in gray."
            )
            classified = filtered.copy()
            classified["class_id"] = -1
            classified["fill_color"] = [
                [150, 150, 150, 90] for _ in range(len(classified))
            ]

    if selected is not None and not selected.empty:
        selected_outside_filter = not selected["zcta"].isin(classified["zcta"]).any()
        if selected_outside_filter:
            st.info("The selected territory is outside the active demographic filters.")

    pois = gpd.GeoDataFrame()
    if enabled_categories:
        bounds = territory_bounds(selected) if selected is not None else DFW_BOUNDS
        try:
            pois = filter_pois(poi_loader(bounds), enabled_categories)
            if pois.empty:
                st.info("No enabled OpenStreetMap POIs were found in this area.")
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"OpenStreetMap POIs are temporarily unavailable: {exc}")

    _render_summary(selected)
    count_columns = st.columns(2)
    count_columns[0].metric("Visible ZCTAs", f"{len(classified):,}")
    count_columns[1].metric("POIs shown", f"{len(pois):,}")

    if bins:
        st.caption(
            f"{metric_label} quantile upper bounds: "
            + " · ".join(_format_metric(value) for value in bins)
        )

    layers = build_layers(
        classified,
        selected=selected,
        pois=pois,
        enabled_poi_categories=enabled_categories,
    )
    deck = build_deck(layers, view_state_for(demographics, selected))
    st.pydeck_chart(deck, width="stretch", height=650, key="territory-map")
    st.caption(
        "OpenStreetMap coverage varies and should not be treated as a complete "
        "market inventory."
    )


def main() -> None:
    """Load configuration and data, then render the application."""
    st.set_page_config(page_title="Texas Territory Heatmapper", layout="wide")
    load_dotenv()
    api_key = os.getenv("CENSUS_API_KEY", "").strip()
    if not api_key:
        st.error(
            "CENSUS_API_KEY is missing. Copy .env.example to .env and add a "
            "Census API key."
        )
        st.stop()

    try:
        demographics = add_metrics(load_census_data(api_key))
    except Exception as exc:
        LOGGER.error("Census load failed: %s", type(exc).__name__)
        st.error(
            "Unable to load Census demographics. Check the API key and network "
            "connection, then try again."
        )
        st.stop()
    render_dashboard(demographics)


if __name__ == "__main__":
    main()

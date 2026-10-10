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
    MISSING_COLOR,
    NAMED_TERRITORIES,
    TerritorySelectionError,
    add_metrics,
    classify_quantiles,
    filter_demographics,
    palette_for_class_count,
    select_territory,
    territory_bounds,
)
from data import BUSINESS_TYPES, load_census_data, load_overpass_pois
from map_view import build_deck, build_layers, elevation_scale_for, view_state_for


METRICS: Final[dict[str, str]] = {
    "Target Kids (0–14)": "target_kids",
    "Family Density %": "family_density_pct",
    "High Income %": "high_income_pct",
    "Median Age": "median_age",
}
DFW_BOUNDS: Final = (32.55, -97.55, 33.45, -96.45)
BUSINESS_LABELS: Final[dict[str, str]] = {
    entry["label"]: category_id for category_id, entry in BUSINESS_TYPES.items()
}
PoiLoader = Callable[..., gpd.GeoDataFrame]
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
    if frame.empty or "category" not in frame.columns:
        return frame.copy()
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


def partial_filter_notice(
    selected: gpd.GeoDataFrame | None,
    classified: gpd.GeoDataFrame,
) -> str | None:
    """Describe a partially filtered selected territory, if any."""
    if selected is None or selected.empty:
        return None
    visible = int(selected["zcta"].isin(classified["zcta"]).sum())
    if 0 < visible < len(selected):
        return f"{visible} of {len(selected)} selected ZCTAs meet filters"
    return None


def legend_rows(
    metric_label: str,
    metric: str,
    bins: list[float],
) -> list[dict[str, Any]]:
    """Build swatch legend rows for quantile bins plus a No data key."""
    del metric_label  # Label is shown by the caller section heading.
    percent = metric.endswith("_pct")
    currency = metric == "median_household_income"

    if not bins:
        return [{"label": "No data", "color": list(MISSING_COLOR)}]

    palette = palette_for_class_count(len(bins))

    rows: list[dict[str, Any]] = []
    previous: float | None = None
    for index, upper in enumerate(bins):
        formatted_upper = _format_metric(upper, currency=currency, percent=percent)
        if previous is None:
            label = f"≤ {formatted_upper}"
        else:
            formatted_lower = _format_metric(
                previous, currency=currency, percent=percent
            )
            label = f"{formatted_lower} – {formatted_upper}"
        rows.append({"label": label, "color": list(palette[index])})
        previous = upper

    rows.append({"label": "No data", "color": list(MISSING_COLOR)})
    return rows


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
    enabled_categories: set[str] = set()
    if st.sidebar.checkbox("Schools"):
        enabled_categories.add("school")
    if st.sidebar.checkbox("Pediatricians / clinics"):
        enabled_categories.add("pediatrician")
    selected_business_labels = st.sidebar.multiselect(
        "Business types",
        options=tuple(BUSINESS_LABELS),
        default=(BUSINESS_TYPES["hairdresser"]["label"],),
    )
    enabled_categories.update(
        BUSINESS_LABELS[label] for label in selected_business_labels
    )
    st.sidebar.caption(
        "Color shows metric quantiles. Polygon height always shows Target Kids. "
        "OpenStreetMap overlays are cached for about six hours per area and selection."
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

    notice = partial_filter_notice(selected, classified)
    if notice:
        st.info(notice)

    pois = gpd.GeoDataFrame()
    if enabled_categories:
        bounds = territory_bounds(selected) if selected is not None else DFW_BOUNDS
        try:
            loaded = poi_loader(bounds, categories=enabled_categories)
            pois = filter_pois(loaded, enabled_categories)
            if pois.empty:
                st.info("No enabled OpenStreetMap POIs were found in this area.")
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"OpenStreetMap POIs are temporarily unavailable: {exc}")

    _render_summary(selected)
    count_columns = st.columns(2)
    count_columns[0].metric("Visible ZCTAs", f"{len(classified):,}")
    count_columns[1].metric("POIs shown", f"{len(pois):,}")

    st.markdown("**Legend**")
    for row in legend_rows(metric_label, metric, bins):
        red, green, blue, alpha = row["color"]
        st.markdown(
            (
                '<div style="display:flex;align-items:center;gap:0.5rem;'
                'margin-bottom:0.25rem">'
                f'<span style="display:inline-block;width:0.9rem;height:0.9rem;'
                f'background:rgba({red},{green},{blue},{alpha / 255:.3f});'
                'border:1px solid #4b5563"></span>'
                f"<span>{row['label']}</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

    elevation_source = classified if not classified.empty else demographics
    layers = build_layers(
        classified,
        selected=selected,
        pois=pois,
        enabled_poi_categories=enabled_categories,
        elevation_scale=elevation_scale_for(elevation_source, selected),
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
    st.set_option("client.toolbarMode", "minimal")
    st.markdown(
        """
        <style>
        [data-testid="stAppDeployButton"] {display: none;}
        [data-testid="stStatusWidget"] button {display: none;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    if not os.environ.get("CENSUS_API_KEY"):
        LOGGER.info(
            "CENSUS_API_KEY not found in OS environment. Loading local .env file..."
        )
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

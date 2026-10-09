"""Cached Census and OpenStreetMap data access."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import censusdis.data as ced
from censusdis.values import ALL_SPECIAL_VALUES
import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point
import streamlit as st


CENSUS_DATASET: Final = "acs/acs5/subject"
CENSUS_VINTAGE: Final = 2024
CACHE_TTL_SECONDS: Final = 86_400
OVERPASS_URL: Final = "https://overpass-api.de/api/interpreter"
OVERPASS_USER_AGENT: Final = (
    "TexasTerritoryHeatmapper/1.0 "
    "(demographic territory research; contact via repository)"
)

CENSUS_COLUMNS: Final[dict[str, str]] = {
    "NAME": "name",
    "ZIP_CODE_TABULATION_AREA": "zcta",
    "S0101_C01_001E": "total_population",
    "S0101_C01_032E": "median_age",
    "S0101_C01_002E": "under_5",
    "S0101_C01_003E": "ages_5_9",
    "S0101_C01_004E": "ages_10_14",
    "S1101_C01_001E": "total_households",
    "S1101_C01_002E": "average_household_size",
    "S1101_C01_003E": "total_family_households",
    "S1101_C02_003E": "married_couple_families",
    "S1901_C01_012E": "median_household_income",
    "S1901_C01_009E": "income_100_149",
    "S1901_C01_010E": "income_150_199",
    "S1901_C01_011E": "income_200_plus",
}
CENSUS_VARIABLES: Final[list[str]] = [
    column for column in CENSUS_COLUMNS if column not in {"ZIP_CODE_TABULATION_AREA"}
]
NUMERIC_COLUMNS: Final[list[str]] = [
    renamed
    for original, renamed in CENSUS_COLUMNS.items()
    if original not in {"NAME", "ZIP_CODE_TABULATION_AREA"}
]
POI_COLUMNS: Final[list[str]] = [
    "name",
    "category",
    "latitude",
    "longitude",
    "geometry",
]


def normalize_census(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Normalize Census columns, numeric values, and coordinate system."""
    missing = sorted(set(CENSUS_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Census response is missing columns: {', '.join(missing)}")
    if frame.crs is None:
        raise ValueError("Census geometry has no coordinate reference system")

    result = frame.rename(columns=CENSUS_COLUMNS).copy()
    result[NUMERIC_COLUMNS] = (
        result[NUMERIC_COLUMNS]
        .replace(list(ALL_SPECIAL_VALUES), pd.NA)
        .apply(pd.to_numeric, errors="coerce")
    )
    result["zcta"] = result["zcta"].astype("string").str.zfill(5)
    return result.to_crs(epsg=4326)


def _download_census(api_key: str) -> gpd.GeoDataFrame:
    """Download Texas-intersecting ZCTAs through censusdis."""
    frame = ced.download(
        CENSUS_DATASET,
        CENSUS_VINTAGE,
        download_variables=CENSUS_VARIABLES,
        zip_code_tabulation_area="*",
        download_contained_within={"state": "48"},
        area_threshold=1e-12,
        with_geometry=True,
        api_key=api_key,
    )
    return normalize_census(frame)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Loading 2024 ACS data…")
def load_census_data(api_key: str) -> gpd.GeoDataFrame:
    """Return cached normalized 2024 ACS demographics for Texas ZCTAs."""
    return _download_census(api_key)


def _format_bound(value: float) -> str:
    return f"{round(float(value), 4):.4f}".rstrip("0").rstrip(".")


def build_overpass_query(
    bounds: tuple[float, float, float, float],
) -> str:
    """Build one query for all supported POI categories."""
    bbox = ",".join(_format_bound(value) for value in bounds)
    return f"""[out:json][timeout:25];
(
  nwr["amenity"="school"]({bbox});
  nwr["healthcare"="paediatrician"]({bbox});
  nwr["amenity"="clinic"]({bbox});
  nwr["shop"="hairdresser"]({bbox});
);
out center;"""


def _poi_category(tags: Mapping[str, Any]) -> str | None:
    if tags.get("amenity") == "school":
        return "school"
    if tags.get("healthcare") == "paediatrician" or tags.get("amenity") == "clinic":
        return "pediatrician"
    if tags.get("shop") == "hairdresser":
        return "competitor"
    return None


def _empty_pois() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=POI_COLUMNS, geometry="geometry", crs="EPSG:4326")


def parse_overpass(payload: Mapping[str, Any]) -> gpd.GeoDataFrame:
    """Normalize Overpass nodes and way/relation centers."""
    if payload.get("remark"):
        raise ValueError(f"Overpass service returned: {payload['remark']}")

    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        raise ValueError("Overpass response 'elements' must be a list")

    rows: list[dict[str, Any]] = []
    fallback_names = {
        "school": "Unnamed school",
        "pediatrician": "Unnamed pediatrician/clinic",
        "competitor": "Unnamed competitor salon",
    }
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        tags = element.get("tags") or {}
        if not isinstance(tags, Mapping):
            continue
        category = _poi_category(tags)
        if category is None:
            continue

        center = element.get("center") or {}
        if not isinstance(center, Mapping):
            center = {}
        latitude = element.get("lat")
        longitude = element.get("lon")
        if latitude is None:
            latitude = center.get("lat")
        if longitude is None:
            longitude = center.get("lon")
        if latitude is None or longitude is None:
            continue
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            continue

        rows.append(
            {
                "name": str(tags.get("name") or fallback_names[category]),
                "category": category,
                "latitude": latitude,
                "longitude": longitude,
                "geometry": Point(longitude, latitude),
            }
        )

    if not rows:
        return _empty_pois()
    return gpd.GeoDataFrame(rows, columns=POI_COLUMNS, geometry="geometry", crs="EPSG:4326")


def _fetch_overpass(
    bounds: tuple[float, float, float, float],
) -> gpd.GeoDataFrame:
    query = build_overpass_query(bounds)
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": OVERPASS_USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("Overpass response must be a JSON object")
    return parse_overpass(payload)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Loading OpenStreetMap POIs…")
def _load_overpass_pois_cached(
    bounds: tuple[float, float, float, float],
) -> gpd.GeoDataFrame:
    return _fetch_overpass(bounds)


def load_overpass_pois(
    bounds: tuple[float, float, float, float],
) -> gpd.GeoDataFrame:
    """Round bounds before using them as the combined POI cache key."""
    rounded = tuple(round(float(value), 4) for value in bounds)
    return _load_overpass_pois_cached(rounded)

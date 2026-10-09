"""Census (cached) and OpenStreetMap (uncached) data access."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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

# Territory aids stay fixed; business types are the curated North Texas catalog.
TERRITORY_AIDS: Final[dict[str, dict[str, Any]]] = {
    "school": {
        "label": "School",
        "selectors": [("amenity", "school")],
        "color": [52, 211, 153, 220],
    },
    "pediatrician": {
        "label": "Pediatrician / clinic",
        "selectors": [("healthcare", "paediatrician"), ("amenity", "clinic")],
        "color": [96, 165, 250, 220],
    },
}
BUSINESS_TYPES: Final[dict[str, dict[str, Any]]] = {
    "hairdresser": {
        "label": "Hair salon",
        "selectors": [("shop", "hairdresser")],
        "color": [244, 114, 182, 220],
    },
    "beauty": {
        "label": "Beauty salon",
        "selectors": [("shop", "beauty")],
        "color": [251, 113, 133, 220],
    },
    "barber": {
        "label": "Barber",
        "selectors": [("shop", "barber")],
        "color": [249, 168, 212, 220],
    },
    "cafe": {
        "label": "Cafe / coffee",
        "selectors": [("amenity", "cafe")],
        "color": [251, 191, 36, 220],
    },
    "restaurant": {
        "label": "Restaurant",
        "selectors": [("amenity", "restaurant")],
        "color": [245, 158, 11, 220],
    },
    "fast_food": {
        "label": "Fast food",
        "selectors": [("amenity", "fast_food")],
        "color": [234, 179, 8, 220],
    },
    "fitness_centre": {
        "label": "Gym / fitness",
        "selectors": [("leisure", "fitness_centre")],
        "color": [16, 185, 129, 220],
    },
    "pharmacy": {
        "label": "Pharmacy",
        "selectors": [("amenity", "pharmacy")],
        "color": [34, 197, 94, 220],
    },
    "childcare": {
        "label": "Childcare / daycare",
        "selectors": [("amenity", "childcare")],
        "color": [125, 211, 252, 220],
    },
    "dentist": {
        "label": "Dentist",
        "selectors": [("amenity", "dentist")],
        "color": [56, 189, 248, 220],
    },
    "veterinary": {
        "label": "Veterinary",
        "selectors": [("amenity", "veterinary")],
        "color": [45, 212, 191, 220],
    },
    "car_repair": {
        "label": "Auto repair",
        "selectors": [("shop", "car_repair")],
        "color": [148, 163, 184, 220],
    },
    "car_wash": {
        "label": "Car wash",
        "selectors": [("amenity", "car_wash")],
        "color": [100, 116, 139, 220],
    },
    "laundry": {
        "label": "Laundry",
        "selectors": [("shop", "laundry")],
        "color": [167, 139, 250, 220],
    },
    "pet": {
        "label": "Pet store",
        "selectors": [("shop", "pet")],
        "color": [192, 132, 252, 220],
    },
    "bakery": {
        "label": "Bakery",
        "selectors": [("shop", "bakery")],
        "color": [251, 146, 60, 220],
    },
    "convenience": {
        "label": "Convenience store",
        "selectors": [("shop", "convenience")],
        "color": [161, 98, 7, 220],
    },
    "estate_agent": {
        "label": "Real estate office",
        "selectors": [("office", "estate_agent")],
        "color": [99, 102, 241, 220],
    },
    "garden_centre": {
        "label": "Garden center",
        "selectors": [("shop", "garden_centre")],
        "color": [22, 163, 74, 220],
    },
}
POI_CATALOG: Final[dict[str, dict[str, Any]]] = {**TERRITORY_AIDS, **BUSINESS_TYPES}
DEFAULT_POI_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"school", "pediatrician", "hairdresser"}
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


def _normalize_categories(categories: Iterable[str] | None) -> set[str]:
    if categories is None:
        return set(DEFAULT_POI_CATEGORIES)
    return {category for category in categories if category in POI_CATALOG}


def build_overpass_query(
    bounds: tuple[float, float, float, float],
    *,
    categories: Iterable[str] | None = None,
) -> str:
    """Build an Overpass query for the requested POI categories only."""
    bbox = ",".join(_format_bound(value) for value in bounds)
    selected = _normalize_categories(categories)
    # Stable order: aids first, then business types in catalog order.
    ordered = [key for key in TERRITORY_AIDS if key in selected] + [
        key for key in BUSINESS_TYPES if key in selected
    ]
    lines: list[str] = []
    for category_id in ordered:
        for key, value in POI_CATALOG[category_id]["selectors"]:
            lines.append(f'  nwr["{key}"="{value}"]({bbox});')
    body = "\n".join(lines) if lines else "  // no categories selected"
    return f"""[out:json][timeout:25];
(
{body}
);
out center;"""


def _poi_category(tags: Mapping[str, Any]) -> str | None:
    """Resolve tags to a catalog category; aids win over business types."""
    for category_id, entry in TERRITORY_AIDS.items():
        for key, value in entry["selectors"]:
            if tags.get(key) == value:
                return category_id
    for category_id, entry in BUSINESS_TYPES.items():
        for key, value in entry["selectors"]:
            if tags.get(key) == value:
                return category_id
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

        label = POI_CATALOG[category]["label"]
        rows.append(
            {
                "name": str(tags.get("name") or f"Unnamed {label.lower()}"),
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
    categories: Iterable[str] | None = None,
) -> gpd.GeoDataFrame:
    query = build_overpass_query(bounds, categories=categories)
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


def load_overpass_pois(
    bounds: tuple[float, float, float, float],
    *,
    categories: Iterable[str] | None = None,
) -> gpd.GeoDataFrame:
    """Fetch POIs for the requested categories (uncached Overpass call)."""
    rounded = tuple(round(float(value), 4) for value in bounds)
    return _fetch_overpass(rounded, categories=_normalize_categories(categories))

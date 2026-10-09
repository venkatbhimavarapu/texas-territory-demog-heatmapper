"""Demographic metrics, classification, filtering, and territory selection."""

from __future__ import annotations

import re
from typing import Final

import geopandas as gpd
import mapclassify
import numpy as np
import pandas as pd


NAMED_TERRITORIES: Final[dict[str, tuple[str, ...]]] = {
    "Flower Mound": ("75022", "75028"),
    "Roanoke": ("76262",),
    "Southlake": ("76092",),
    "Grapevine": ("76051",),
    "Keller": ("76244", "76248"),
    "Coppell": ("75019",),
    "Lewisville": ("75057", "75067", "75077"),
    "Trophy Club": ("76262",),
}

COLOR_PALETTE: Final[list[list[int]]] = [
    [255, 247, 188, 190],
    [254, 196, 79, 200],
    [254, 153, 41, 210],
    [236, 112, 20, 220],
    [204, 76, 2, 230],
]
MISSING_COLOR: Final[list[int]] = [150, 150, 150, 90]


class TerritorySelectionError(ValueError):
    """Raised when a territory selection is invalid or unavailable."""


def add_metrics(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy with the three derived targeting metrics."""
    result = frame.copy()
    result["target_kids"] = result[
        ["under_5", "ages_5_9", "ages_10_14"]
    ].sum(axis=1, min_count=3)

    denominator = result["total_households"].replace(0, np.nan)
    result["family_density_pct"] = (
        result["total_family_households"] / denominator * 100
    )
    result["high_income_pct"] = result[
        ["income_100_149", "income_150_199", "income_200_plus"]
    ].sum(axis=1, min_count=3)
    return result


def filter_demographics(
    frame: gpd.GeoDataFrame,
    minimum_income: float,
    minimum_target_kids: float,
) -> gpd.GeoDataFrame:
    """Apply the two minimum demographic filters."""
    mask = (frame["median_household_income"] >= minimum_income) & (
        frame["target_kids"] >= minimum_target_kids
    )
    return frame.loc[mask].copy()


def classify_quantiles(
    frame: gpd.GeoDataFrame, metric: str, maximum_classes: int = 5
) -> tuple[gpd.GeoDataFrame, list[float]]:
    """Classify a metric into adaptive quantiles and attach RGBA colors."""
    if metric not in frame:
        raise KeyError(metric)

    numeric = pd.to_numeric(frame[metric], errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        raise ValueError(f"{metric} has no usable values")

    class_count = min(maximum_classes, int(valid.nunique()))
    if class_count == 1:
        classes = pd.Series(0, index=valid.index, dtype=int)
        bins = [float(valid.iloc[0])]
    else:
        classifier = mapclassify.Quantiles(valid.to_numpy(), k=class_count)
        classes = pd.Series(classifier.yb, index=valid.index, dtype=int)
        bins = [float(value) for value in classifier.bins]

    palette_indexes = np.linspace(0, len(COLOR_PALETTE) - 1, class_count).astype(int)
    palette = [COLOR_PALETTE[index] for index in palette_indexes]

    result = frame.copy()
    result["class_id"] = -1
    result.loc[classes.index, "class_id"] = classes
    result["class_id"] = result["class_id"].astype(int)
    result["fill_color"] = [
        list(MISSING_COLOR) if class_id < 0 else list(palette[class_id])
        for class_id in result["class_id"]
    ]
    return result, bins


def select_territory(
    frame: gpd.GeoDataFrame,
    *,
    named_place: str | None = None,
    zcta: str | None = None,
) -> gpd.GeoDataFrame:
    """Select a named territory or one exact ZCTA from the available data."""
    if bool(named_place) == bool(zcta):
        raise TerritorySelectionError("Choose one named place or one ZCTA")

    if named_place:
        if named_place not in NAMED_TERRITORIES:
            raise TerritorySelectionError(f"Unknown named place: {named_place}")
        zctas = NAMED_TERRITORIES[named_place]
    else:
        assert zcta is not None
        if re.fullmatch(r"\d{5}", zcta) is None:
            raise TerritorySelectionError("ZCTA must contain exactly five digits")
        zctas = (zcta,)

    selected = frame.loc[frame["zcta"].isin(zctas)].copy()
    if selected.empty:
        label = named_place or zcta
        raise TerritorySelectionError(f"Territory is unavailable: {label}")
    return selected


def territory_bounds(frame: gpd.GeoDataFrame) -> tuple[float, float, float, float]:
    """Return bounds in Overpass order: south, west, north, east."""
    if frame.empty:
        raise TerritorySelectionError("Cannot derive bounds from an empty territory")
    west, south, east, north = frame.total_bounds
    return float(south), float(west), float(north), float(east)

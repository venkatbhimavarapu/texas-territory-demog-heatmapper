import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from analysis import (
    NAMED_TERRITORIES,
    TerritorySelectionError,
    add_metrics,
    classify_quantiles,
    filter_demographics,
    select_territory,
    territory_bounds,
)


def demographic_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "zcta": ["75022", "75028", "76262"],
            "under_5": [10.0, 20.0, 5.0],
            "ages_5_9": [11.0, 21.0, 6.0],
            "ages_10_14": [12.0, 22.0, 7.0],
            "total_households": [100.0, 0.0, 50.0],
            "total_family_households": [60.0, 0.0, 30.0],
            "income_100_149": [10.0, 20.0, 30.0],
            "income_150_199": [5.0, 10.0, 15.0],
            "income_200_plus": [2.0, 4.0, 6.0],
            "median_household_income": [120_000.0, 90_000.0, 150_000.0],
        },
        geometry=[
            box(-97.0, 32.9, -96.9, 33.0),
            box(-97.1, 33.0, -97.0, 33.1),
            box(-97.3, 33.0, -97.1, 33.2),
        ],
        crs="EPSG:4326",
    )


def test_add_metrics_computes_targets_and_avoids_zero_division():
    result = add_metrics(demographic_frame())

    assert result["target_kids"].tolist() == [33.0, 63.0, 18.0]
    assert result.loc[0, "family_density_pct"] == 60.0
    assert math.isnan(result.loc[1, "family_density_pct"])
    assert result["high_income_pct"].tolist() == [17.0, 34.0, 51.0]


def test_add_metrics_requires_all_source_age_bands():
    frame = demographic_frame()
    frame.loc[0, "under_5"] = math.nan

    result = add_metrics(frame)

    assert math.isnan(result.loc[0, "target_kids"])


def test_filter_demographics_applies_both_minimums():
    frame = add_metrics(demographic_frame())

    result = filter_demographics(
        frame, minimum_income=100_000, minimum_target_kids=20
    )

    assert result["zcta"].tolist() == ["75022"]


def test_quantiles_adapt_to_distinct_values_and_preserve_missing_rows():
    frame = gpd.GeoDataFrame(
        {"metric": [10.0, 10.0, 20.0, math.nan]},
        geometry=[box(i, 0, i + 0.5, 0.5) for i in range(4)],
        crs="EPSG:4326",
    )

    result, bins = classify_quantiles(frame, "metric")

    assert len(bins) == 2
    assert set(result.loc[:2, "class_id"]) == {0, 1}
    assert result.loc[3, "class_id"] == -1
    assert all(len(color) == 4 for color in result["fill_color"])


def test_quantiles_handle_one_value_and_reject_all_missing():
    frame = gpd.GeoDataFrame(
        {"metric": [7.0, 7.0]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
        crs="EPSG:4326",
    )

    result, bins = classify_quantiles(frame, "metric")

    assert bins == [7.0]
    assert result["class_id"].tolist() == [0, 0]

    with pytest.raises(ValueError, match="no usable values"):
        classify_quantiles(frame.assign(metric=math.nan), "metric")


def test_named_territories_include_approved_places():
    assert set(NAMED_TERRITORIES) == {
        "Flower Mound",
        "Roanoke",
        "Southlake",
        "Grapevine",
        "Keller",
        "Coppell",
        "Lewisville",
        "Trophy Club",
    }


def test_territory_selection_supports_names_and_exact_zctas():
    frame = demographic_frame()

    flower_mound = select_territory(frame, named_place="Flower Mound")
    direct = select_territory(frame, zcta="76262")

    assert flower_mound["zcta"].tolist() == ["75022", "75028"]
    assert direct["zcta"].tolist() == ["76262"]


@pytest.mark.parametrize("zcta", ["", "1234", "abcde", "99999"])
def test_territory_selection_rejects_invalid_or_unavailable_zctas(zcta):
    with pytest.raises(TerritorySelectionError):
        select_territory(demographic_frame(), zcta=zcta)


def test_territory_bounds_return_overpass_order():
    selected = select_territory(demographic_frame(), named_place="Flower Mound")

    assert territory_bounds(selected) == (32.9, -97.1, 33.1, -96.9)

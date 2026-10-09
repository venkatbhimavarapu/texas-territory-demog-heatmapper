import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

import data


def raw_census_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "NAME": ["ZCTA5 75022"],
            "ZIP_CODE_TABULATION_AREA": ["75022"],
            "S0101_C01_001E": ["25000"],
            "S0101_C01_032E": ["39.5"],
            "S0101_C01_002E": ["1000"],
            "S0101_C01_003E": ["1100"],
            "S0101_C01_004E": ["1200"],
            "S1101_C01_001E": ["9000"],
            "S1101_C01_002E": ["2.75"],
            "S1101_C01_003E": ["6500"],
            "S1101_C02_003E": ["5000"],
            "S1901_C01_012E": ["125000"],
            "S1901_C01_009E": ["15.0"],
            "S1901_C01_010E": ["10.0"],
            "S1901_C01_011E": [-666666666],
        },
        geometry=[Point(-97.0, 33.0)],
        crs="EPSG:4326",
    )


def test_normalize_census_renames_and_coerces_estimates():
    result = data.normalize_census(raw_census_frame())

    assert result.loc[0, "zcta"] == "75022"
    assert result.loc[0, "total_population"] == 25_000.0
    assert result.loc[0, "median_age"] == 39.5
    assert result.loc[0, "married_couple_families"] == 5_000.0
    assert math.isnan(result.loc[0, "income_200_plus"])
    assert result.crs.to_epsg() == 4326


def test_normalize_census_reprojects_geometry():
    source = raw_census_frame().to_crs("EPSG:3857")

    result = data.normalize_census(source)

    assert result.crs.to_epsg() == 4326
    assert result.geometry.x.iloc[0] == pytest.approx(-97.0)


def test_download_census_uses_supported_texas_containment(monkeypatch):
    observed = {}

    def fake_download(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return raw_census_frame()

    monkeypatch.setattr(data.ced, "download", fake_download)

    result = data._download_census("test-key")

    assert len(result) == 1
    assert observed["args"][:2] == ("acs/acs5/subject", 2024)
    assert observed["kwargs"]["zip_code_tabulation_area"] == "*"
    assert observed["kwargs"]["with_geometry"] is True
    assert observed["kwargs"]["download_contained_within"] == {"state": "48"}
    assert observed["kwargs"]["api_key"] == "test-key"


def overpass_payload():
    return {
        "elements": [
            {
                "type": "node",
                "lat": 33.01,
                "lon": -97.01,
                "tags": {"amenity": "school", "name": "Oak School"},
            },
            {
                "type": "way",
                "center": {"lat": 33.02, "lon": -97.02},
                "tags": {"healthcare": "paediatrician", "name": "Kids Health"},
            },
            {
                "type": "relation",
                "center": {"lat": 33.03, "lon": -97.03},
                "tags": {"shop": "hairdresser", "name": "Quick Cuts"},
            },
            {
                "type": "node",
                "lat": 33.04,
                "lon": -97.04,
                "tags": {"amenity": "bench"},
            },
        ]
    }


def test_parse_overpass_handles_nodes_and_centers():
    result = data.parse_overpass(overpass_payload())

    assert result["category"].tolist() == [
        "school",
        "pediatrician",
        "competitor",
    ]
    assert result["name"].tolist() == ["Oak School", "Kids Health", "Quick Cuts"]
    assert result.crs.to_epsg() == 4326
    assert result.geometry.x.tolist() == [-97.01, -97.02, -97.03]


def test_parse_overpass_uses_fallback_names_and_handles_empty_payload():
    payload = {
        "elements": [
            {
                "type": "node",
                "lat": 33.01,
                "lon": -97.01,
                "tags": {"amenity": "clinic"},
            }
        ]
    }

    result = data.parse_overpass(payload)
    empty = data.parse_overpass({"elements": []})

    assert result.loc[0, "name"] == "Unnamed pediatrician/clinic"
    assert list(empty.columns) == ["name", "category", "latitude", "longitude", "geometry"]
    assert empty.empty


def test_parse_overpass_rejects_malformed_elements():
    with pytest.raises(ValueError, match="elements"):
        data.parse_overpass({"elements": "not-a-list"})


def test_fetch_overpass_rounds_bounds_and_posts_once(monkeypatch):
    observed = {}

    class FakeResponse:
        def raise_for_status(self):
            observed["status_checked"] = True

        def json(self):
            return overpass_payload()

    def fake_post(url, **kwargs):
        observed["url"] = url
        observed["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr(data.requests, "post", fake_post)

    result = data._fetch_overpass((32.900004, -97.100004, 33.100004, -96.900004))

    assert len(result) == 3
    assert observed["url"].startswith("https://")
    assert observed["kwargs"]["timeout"] == 30
    assert "TexasTerritoryHeatmapper" in observed["kwargs"]["headers"]["User-Agent"]
    query = observed["kwargs"]["data"]["data"]
    assert "(32.9,-97.1,33.1,-96.9)" in query
    assert 'nwr["amenity"="school"]' in query
    assert observed["status_checked"] is True

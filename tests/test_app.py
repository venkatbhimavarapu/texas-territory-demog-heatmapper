import geopandas as gpd
from shapely.geometry import Point, box
from streamlit.testing.v1 import AppTest

import app
from analysis import add_metrics


def dashboard_frame() -> gpd.GeoDataFrame:
    return add_metrics(
        gpd.GeoDataFrame(
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
    )


def dashboard_pois() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["Oak School", "Quick Cuts"],
            "category": ["school", "competitor"],
            "latitude": [33.0, 33.02],
            "longitude": [-97.0, -97.02],
        },
        geometry=[Point(-97.0, 33.0), Point(-97.02, 33.02)],
        crs="EPSG:4326",
    )


def test_territory_summary_aggregates_counts_and_household_ratio():
    summary = app.territory_summary(dashboard_frame())

    assert summary["total_population"] == 50_000
    assert summary["target_kids"] == 8_100
    assert summary["median_household_income"] == 130_000
    assert summary["family_density_pct"] == 12_000 / 18_000 * 100


def test_filter_pois_returns_only_enabled_categories():
    result = app.filter_pois(dashboard_pois(), {"school"})

    assert result["name"].tolist() == ["Oak School"]


def test_render_dashboard_builds_with_fixture_data():
    def fixture_loader(_bounds):
        return dashboard_pois()

    test_app = AppTest.from_function(
        app.render_dashboard,
        args=(dashboard_frame(),),
        kwargs={"poi_loader": fixture_loader},
    )
    test_app.run()

    assert not test_app.exception
    assert test_app.title[0].value == "Texas Territory Demographic Heatmapper"
    assert len(test_app.metric) >= 2

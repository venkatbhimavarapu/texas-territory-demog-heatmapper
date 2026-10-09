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
    def smoke_dashboard():
        import app
        import geopandas as gpd
        from shapely.geometry import box

        frame = app.add_metrics(
            gpd.GeoDataFrame(
                {
                    "zcta": ["75022"],
                    "total_population": [20_000.0],
                    "median_age": [38.0],
                    "median_household_income": [120_000.0],
                    "under_5": [1_000.0],
                    "ages_5_9": [1_100.0],
                    "ages_10_14": [1_200.0],
                    "total_households": [8_000.0],
                    "total_family_households": [5_000.0],
                    "income_100_149": [15.0],
                    "income_150_199": [10.0],
                    "income_200_plus": [8.0],
                },
                geometry=[box(-97.1, 32.9, -97.0, 33.0)],
                crs="EPSG:4326",
            )
        )

        def no_pois(_bounds):
            return gpd.GeoDataFrame()

        app.render_dashboard(frame, poi_loader=no_pois)

    test_app = AppTest.from_function(smoke_dashboard)
    test_app.run()

    assert not test_app.exception
    assert test_app.title[0].value == "Texas Territory Demographic Heatmapper"
    assert len(test_app.metric) >= 2


def test_all_missing_primary_metric_is_non_crashing():
    def smoke_missing_metric():
        import app
        import geopandas as gpd
        from shapely.geometry import box

        frame = app.add_metrics(
            gpd.GeoDataFrame(
                {
                    "zcta": ["75022"],
                    "total_population": [20_000.0],
                    "median_age": [float("nan")],
                    "median_household_income": [120_000.0],
                    "under_5": [1_000.0],
                    "ages_5_9": [1_100.0],
                    "ages_10_14": [1_200.0],
                    "total_households": [8_000.0],
                    "total_family_households": [5_000.0],
                    "income_100_149": [15.0],
                    "income_150_199": [10.0],
                    "income_200_plus": [8.0],
                },
                geometry=[box(-97.1, 32.9, -97.0, 33.0)],
                crs="EPSG:4326",
            )
        )
        app.render_dashboard(frame)

    test_app = AppTest.from_function(smoke_missing_metric).run()
    test_app.selectbox[0].select("Median Age").run()

    assert not test_app.exception
    assert any("no usable values" in warning.value.lower() for warning in test_app.warning)


def test_census_failure_does_not_expose_api_key():
    def smoke_census_failure():
        import os
        import app

        os.environ["CENSUS_API_KEY"] = "super-secret-key"

        def fail_with_key(_api_key):
            raise RuntimeError(
                "https://api.census.gov/data?key=super-secret-key failed"
            )

        app.load_census_data = fail_with_key
        app.main()

    test_app = AppTest.from_function(smoke_census_failure).run()

    assert not test_app.exception
    assert test_app.error
    assert "super-secret-key" not in test_app.error[0].value

def test_partial_filter_notice_reports_visible_count():
    selected = dashboard_frame()
    classified = selected.iloc[[0]]

    notice = app.partial_filter_notice(selected, classified)

    assert notice == "1 of 2 selected ZCTAs meet filters"


def test_partial_filter_notice_skips_full_and_empty_matches():
    selected = dashboard_frame()

    assert app.partial_filter_notice(selected, selected) is None
    assert app.partial_filter_notice(selected, selected.iloc[0:0]) is None
    assert app.partial_filter_notice(None, selected) is None


def test_legend_rows_include_swatches_ranges_and_missing_key():
    rows = app.legend_rows("High Income %", "high_income_pct", [10.0, 20.0, 35.0])

    assert rows[0]["label"] == "≤ 10.0%"
    assert rows[1]["label"] == "10.0% – 20.0%"
    assert rows[-1]["label"] == "No data"
    assert rows[-1]["color"] == [150, 150, 150, 90]
    assert all("color" in row for row in rows)

def test_render_dashboard_passes_adaptive_elevation():
    import json
    from pathlib import Path

    capture_file = Path("/tmp/texas_heatmapper_elevation_scale.json")
    capture_file.unlink(missing_ok=True)

    def smoke():
        import json
        from pathlib import Path

        import app
        import geopandas as gpd
        from shapely.geometry import box

        def fake_build_layers(*args, **kwargs):
            Path("/tmp/texas_heatmapper_elevation_scale.json").write_text(
                json.dumps({"elevation_scale": kwargs.get("elevation_scale")})
            )
            return []

        app.build_layers = fake_build_layers
        frame = app.add_metrics(
            gpd.GeoDataFrame(
                {
                    "zcta": ["75022"],
                    "total_population": [20_000.0],
                    "median_age": [38.0],
                    "median_household_income": [120_000.0],
                    "under_5": [1_000.0],
                    "ages_5_9": [1_100.0],
                    "ages_10_14": [1_200.0],
                    "total_households": [8_000.0],
                    "total_family_households": [5_000.0],
                    "income_100_149": [15.0],
                    "income_150_199": [10.0],
                    "income_200_plus": [8.0],
                },
                geometry=[box(-97.1, 32.9, -97.0, 33.0)],
                crs="EPSG:4326",
            )
        )
        app.render_dashboard(frame, poi_loader=lambda _b: gpd.GeoDataFrame())

    AppTest.from_function(smoke).run()
    observed = json.loads(capture_file.read_text())
    assert "elevation_scale" in observed
    assert observed["elevation_scale"] is not None
    assert observed["elevation_scale"] > 0


def test_legend_colors_match_classified_fill_colors_with_ties():
    """Legend palette must match classify_quantiles when mapclassify collapses bins."""
    from analysis import classify_quantiles

    frame = gpd.GeoDataFrame(
        {
            "zcta": [str(i) for i in range(10)],
            "high_income_pct": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0],
        },
        geometry=[box(i, 0, i + 0.5, 0.5) for i in range(10)],
        crs="EPSG:4326",
    )
    classified, bins = classify_quantiles(frame, "high_income_pct")
    rows = app.legend_rows("High Income %", "high_income_pct", bins)

    for class_id, color in (
        classified.loc[classified["class_id"] >= 0, ["class_id", "fill_color"]]
        .drop_duplicates("class_id")
        .itertuples(index=False)
    ):
        assert rows[int(class_id)]["color"] == list(color)


# Deferred Review Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four deferred review findings: partial-territory filter notices, swatch legends, broad hairdresser matching, and adaptive Target Kids elevation.

**Architecture:** Keep the existing four-module layout. Pure helpers land in `app.py` (filter messaging + legend rows), `data.py` (Overpass query), and `map_view.py` (elevation scale + layer wiring). No new modules, dependencies, or network endpoints.

**Tech Stack:** Streamlit, GeoPandas, Pydeck, pytest, existing Overpass HTTPS client

**Spec:** `docs/superpowers/specs/2026-10-09-texas-territory-heatmapper-design.md` (Review follow-up section)

## Global Constraints

- Do not add sidebar controls, new dependencies, or live API calls in tests.
- Competitor POIs are every `shop=hairdresser` element; do not restore name-regex matching.
- Elevation stays tied to `target_kids`; only the scale factor changes.
- Legend palette must reuse `COLOR_PALETTE` / `MISSING_COLOR` from `analysis.py`.
- Preserve existing non-crashing failure behavior and all current green tests.
- Branch remains `cursor/texas-heatmapper-9153`; commit after each task.

## Review Focus

- Empty demographics when computing elevation scale → return a safe clamped default, never divide by zero.
- Selected territory with all-NaN Target Kids → fall back to visible demographics, then to the clamped default.
- Metric format for legend ranges → currency for income metrics, percent for density/high-income, plain for counts/age.
- Partial filter where zero selected ZCTAs remain → keep the existing “outside filters” message; do not also emit the “X of Y” notice.
- Existing Overpass parse tests still accept hairdresser competitors without name filters.

## File map

- Modify: `data.py` — broaden hairdresser Overpass selector
- Modify: `map_view.py` — adaptive elevation scale; pass it into `PolygonLayer`
- Modify: `app.py` — partial-filter notice helper; legend row builder; wire both into `render_dashboard`
- Modify: `tests/test_data.py`, `tests/test_map_view.py`, `tests/test_app.py`

---

### Task 1: Broaden competitor hairdresser matching

**Files:**
- Modify: `data.py` (`build_overpass_query`)
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: existing `build_overpass_query(bounds: tuple[float, float, float, float]) -> str`
- Produces: query string containing `nwr["shop"="hairdresser"](bbox)` and **not** containing `name~`

- [ ] **Step 1: Write the failing test**

```python
def test_overpass_query_includes_all_hairdressers():
    query = data.build_overpass_query((32.9, -97.1, 33.1, -96.9))

    assert 'nwr["shop"="hairdresser"](32.9,-97.1,33.1,-96.9)' in query
    assert "name~" not in query
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_data.py::test_overpass_query_includes_all_hairdressers -v`
Expected: FAIL because the current query still includes `name~"Salon|Cuts|Hair"`

- [ ] **Step 3: Implement the query change in `data.py`**

Replace the competitor line in `build_overpass_query` with:
`nwr["shop"="hairdresser"]({bbox});`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_data.py -q`
Expected: PASS (including existing competitor parse coverage)

- [ ] **Step 5: Commit**

```bash
git add data.py tests/test_data.py
git commit -m "fix: include all OSM hairdressers as competitors"
```

---

### Task 2: Adaptive Target Kids elevation scale

**Files:**
- Modify: `map_view.py`
- Test: `tests/test_map_view.py`

**Interfaces:**
- Consumes: demographics / selected GeoDataFrames with `target_kids`
- Produces:
  - `elevation_scale_for(demographics: gpd.GeoDataFrame, selected: gpd.GeoDataFrame | None = None) -> float`
  - `build_layers(..., elevation_scale: float | None = None)` uses that scale on the base polygon layer

- [ ] **Step 1: Write the failing tests**

```python
def test_elevation_scale_uses_selected_territory_percentile():
    demographics = mapped_frame()
    selected = demographics.iloc[[0]].copy()
    selected.loc[selected.index[0], "target_kids"] = 1_000.0

    scale = elevation_scale_for(demographics, selected)

    assert scale == pytest.approx(500.0 / 1_000.0)


def test_elevation_scale_clamps_empty_or_missing_target_kids():
    empty = mapped_frame().iloc[0:0]
    missing = mapped_frame().assign(target_kids=float("nan"))

    assert elevation_scale_for(empty) == 0.05
    assert elevation_scale_for(missing) == 0.05


def test_build_layers_applies_provided_elevation_scale():
    layers = build_layers(mapped_frame(), elevation_scale=0.25)
    payload = json.loads(layers[0].to_json())

    assert payload["elevationScale"] == 0.25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_map_view.py::test_elevation_scale_uses_selected_territory_percentile tests/test_map_view.py::test_elevation_scale_clamps_empty_or_missing_target_kids tests/test_map_view.py::test_build_layers_applies_provided_elevation_scale -v`
Expected: FAIL with missing `elevation_scale_for` / unexpected elevationScale

- [ ] **Step 3: Implement `elevation_scale_for` and wire `build_layers`**

Exact behavior:
- Prefer `selected["target_kids"]` when selected is non-empty and has at least one finite value; otherwise use `demographics["target_kids"]`.
- Reference = 95th percentile of the chosen finite series.
- `scale = 500.0 / reference`, then clamp to `[0.05, 5.0]`.
- If no finite values exist, return `0.05`.
- `build_layers` computes the scale via `elevation_scale_for` when `elevation_scale` is `None`; otherwise uses the provided float on the demographic `PolygonLayer`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_map_view.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add map_view.py tests/test_map_view.py
git commit -m "feat: scale polygon elevation from territory kids"
```

---

### Task 3: Partial-filter notice and swatch legend

**Files:**
- Modify: `app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `COLOR_PALETTE`, `MISSING_COLOR` from `analysis`; classified frame; selected frame; bins list
- Produces:
  - `partial_filter_notice(selected: gpd.GeoDataFrame | None, classified: gpd.GeoDataFrame) -> str | None`
  - `legend_rows(metric_label: str, metric: str, bins: list[float]) -> list[dict]`
  - `render_dashboard` shows the notice and renders legend swatches

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_app.py::test_partial_filter_notice_reports_visible_count tests/test_app.py::test_partial_filter_notice_skips_full_and_empty_matches tests/test_app.py::test_legend_rows_include_swatches_ranges_and_missing_key -v`
Expected: FAIL with missing helpers

- [ ] **Step 3: Implement helpers and wire `render_dashboard`**

Exact copy / behavior:
- `partial_filter_notice`: if selected is None/empty → `None`. Let `visible = selected["zcta"].isin(classified["zcta"]).sum()`. If `0 < visible < len(selected)` → `f"{visible} of {len(selected)} selected ZCTAs meet filters"`; else `None`. Keep the existing all-excluded info message untouched.
- `legend_rows`: if `bins` empty → `[{"label": "No data", "color": MISSING_COLOR}]`. Otherwise build one row per bin using the same adaptive palette indexing as `classify_quantiles`, with lower bound = previous bin (or open-ended for the first). Format with `_format_metric(..., percent=metric.endswith("_pct"), currency=metric == "median_household_income")`. Always append the No data row last.
- In `render_dashboard`, after the all-excluded check, if `partial_filter_notice(...)` returns text, `st.info(...)`. Replace the plain quantile caption with a short “Legend” section that loops legend rows and shows a colored swatch plus label.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_app.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat: add partial-filter notice and swatch legend"
```

---

### Task 4: Wire elevation into the dashboard and verify the suite

**Files:**
- Modify: `app.py` (`render_dashboard` layer construction)
- Test: `tests/test_app.py`
- Verify: full suite + compile/import

**Interfaces:**
- Consumes: `elevation_scale_for` / `build_layers(..., elevation_scale=...)` from Task 2
- Produces: dashboard map uses adaptive elevation without changing public loader APIs

- [ ] **Step 1: Write the failing wiring assertion**

```python
def test_render_dashboard_passes_adaptive_elevation(monkeypatch):
    observed = {}

    def fake_build_layers(*args, **kwargs):
        observed.update(kwargs)
        return []

    def smoke():
        import app
        from shapely.geometry import box
        import geopandas as gpd

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
    assert "elevation_scale" in observed
    assert observed["elevation_scale"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_app.py::test_render_dashboard_passes_adaptive_elevation -v`
Expected: FAIL because `render_dashboard` does not yet pass `elevation_scale`

- [ ] **Step 3: Wire elevation in `render_dashboard`**

When calling `build_layers`, pass `elevation_scale=elevation_scale_for(classified if not classified.empty else demographics, selected)`.

- [ ] **Step 4: Run full verification**

Run:
```bash
python3 -m pytest -q
python3 -m compileall -q app.py analysis.py data.py map_view.py
python3 -c 'import app, analysis, data, map_view'
```
Expected: all tests PASS; compile/import succeed

- [ ] **Step 5: Commit and push**

```bash
git add app.py tests/test_app.py
git commit -m "feat: wire adaptive elevation into dashboard"
git push -u origin cursor/texas-heatmapper-9153
```

---

## Spec coverage checklist

- Partial “X of Y selected ZCTAs meet filters” → Task 3
- Swatch legend with ranges + No data → Task 3
- All `shop=hairdresser` competitors → Task 1
- Adaptive clamped Target Kids elevation → Tasks 2 and 4
- Offline tests for each refinement → Tasks 1–4

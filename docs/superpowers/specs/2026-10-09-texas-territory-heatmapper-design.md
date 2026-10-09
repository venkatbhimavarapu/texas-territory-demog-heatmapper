# Texas Territory Demographic Heatmapper Design

## Goal

Build a performant Streamlit dashboard for evaluating Texas retail franchise
territories. The dashboard combines 2024 ACS 5-year demographic estimates for
Texas ZCTAs with OpenStreetMap schools, pediatricians/clinics, and competitor
hair salons.

## Architecture

The application uses four focused modules:

- `app.py` owns Streamlit controls, state, summaries, and user-facing errors.
- `data.py` owns cached Census and Overpass network access and normalization.
- `analysis.py` owns computed metrics, filters, classification, and territory
  lookup.
- `map_view.py` owns Pydeck view state, layers, colors, and tooltips.

Network calls and transformation logic remain independently testable. The app
does not add a database, authentication, exports, or deployment concerns.

## Census data flow

The Census loader queries the 2024 `acs/acs5/subject` dataset through
`censusdis`. ZCTAs are not ordinary children of states, so the loader uses
`download_contained_within={"state": "48"}` with a positive intersection
threshold rather than the invalid `state="48"`/ZCTA hierarchy. This is the
supported `censusdis` equivalent of asking for Texas-intersecting ZCTAs while
also retrieving geometry. It requests only the estimates needed from S0101,
S1101, and S1901, includes geometry, and caches the result for 24 hours.

The loader replaces Census sentinel values with missing values, coerces
estimate columns to numeric values, gives columns stable display-oriented
names, and converts geometry to EPSG:4326.

Analysis computes:

- Target Kids (0–14): under 5 + ages 5–9 + ages 10–14.
- Family Density: total family households / total households × 100.
- High Income: household shares for $100k–$149,999, $150k–$199,999, and
  $200k+.

Filters are applied before classification. The selected metric is divided into
up to five quantile classes; fewer classes are used when fewer distinct values
exist. Missing values are excluded from classification without crashing.

## Territory selection

Users select either an exact five-digit ZCTA or one of these named North Texas
places: Flower Mound, Roanoke, Southlake, Grapevine, Keller, Coppell,
Lewisville, and Trophy Club. Named places map to explicit ZCTA lists; no live
geocoder is required.

The selected territory controls map focus, summary metrics, the highlight
outline, and the POI query bounds. The outline remains visible when active
demographic filters exclude the selected ZCTA, and the UI explains that state.

## OpenStreetMap data flow

When any POI overlay is enabled, one HTTPS POST requests only the enabled
territory aids and selected business types from Overpass for the selected
territory's bounding box. With no selected territory, the request uses a fixed
DFW bounding box. Overpass/POI responses are Streamlit-cached for six hours,
keyed by rounded bounds and the sorted selected category set, so repeat
toggles reuse a prior fetch. HTTP 429 responses pause briefly and retry once.
Census demographics remain cached for 24 hours. Bounds are still rounded for
stable query strings.

Territory aids are schools and pediatricians/clinics. Business overlays come
from a curated North Texas catalog (hair salons, beauty, barber, cafe,
restaurant, fast food, gym, pharmacy, childcare, dentist, veterinary, auto
repair, car wash, laundry, pet store, bakery, convenience, real estate office,
garden center). Default business selection is hair salon. The query uses `nwr`
selectors and `out center` so nodes, ways, and relations can be displayed. A
descriptive User-Agent, timeout, status checks, and JSON validation protect
the public service. Results normalize to name, category, latitude, and
longitude. OpenStreetMap coverage is described as indicative, not a complete
market inventory.

## User interface

The sidebar provides:

- primary metric selection;
- minimum median household income and Target Kids filters;
- named-place or ZCTA territory selection;
- toggles for schools and pediatricians/clinics;
- a multiselect of curated business types (default: Hair salon); and
- a compact explanation of color quantiles and polygon elevation.

The main area provides selected-territory summary cards, filtered ZCTA and POI
counts, and a pitched Pydeck map. Polygon colors represent the selected metric;
elevation always represents Target Kids. Separate layers render the territory
outline and category-colored POIs. Polygon tooltips show the demographic
breakdown, while POI tooltips show name and category.

Missing Census credentials or a Census load failure blocks rendering with an
actionable message. Invalid territories, empty filters, empty POI responses,
and Overpass errors produce non-crashing notices. Overpass failures never
remove an otherwise usable demographic map.

## Review follow-up

Four review findings extend the initial interface without adding controls:

- When only part of a selected named territory meets the active filters, the
  app reports the visible count as “X of Y selected ZCTAs meet filters.” The
  full selected outline remains visible for context.
- The metric legend uses the same palette as the polygon layer. It shows a
  swatch and lower/upper range for each quantile, formats currency and
  percentage metrics appropriately, and includes a gray “No data” key.
- Hair-salon coverage includes every OpenStreetMap element tagged
  `shop=hairdresser`; business-name matching no longer excludes hairdressers
  whose names omit “Salon,” “Cuts,” or “Hair.” The competitor checkbox is
  replaced by the curated business-type multiselect above.
- Polygon elevation remains tied to Target Kids, but its scale is calculated
  from the selected territory’s 95th percentile when a territory is selected,
  otherwise from the visible ZCTAs. The scale is clamped so sparse or outlier
  data cannot create flat or extreme geometry.

These refinements remain independently testable in `app.py`, `data.py`, and
`map_view.py`. They do not introduce new dependencies.

## Verification

Automated tests use local fixtures and mocked network boundaries. They cover:

- Census normalization and sentinel handling;
- metric formulas and zero-household behavior;
- quantile edge cases;
- named-place/ZCTA lookup and territory bounds;
- Overpass nodes, way/relation centers, categories, and malformed responses;
- map layer construction, adaptive elevation, tooltip fields, and legends;
- partial selected-territory filtering and broad hairdresser queries; and
- an application import/build smoke path.

Acceptance requires a green offline test suite, successful Python
compile/import checks, and a Streamlit startup smoke test. With live
credentials, filters and metric changes must reuse cached Census data,
territory selection must highlight and summarize the area, and repeated POI
selections for the same bounds and category set must reuse the cached
Overpass response within the six-hour TTL.

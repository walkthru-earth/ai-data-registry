"""Extract global weather and air quality data from Open-Meteo API.

Uses a world cities dataset (population >= 100K) for ~6,000 cities across
171 countries. City list is loaded at runtime from a public parquet file,
not hardcoded.

Three data streams, each producing a separate GeoParquet file:

1. **weather_hourly**: 24h hourly weather for all cities.
   30 variables: temperature, humidity, wind, precipitation, cloud cover,
   pressure, UV, solar radiation, soil conditions, weather codes.
   Dedup key: (city, country_code, time).

2. **weather_daily**: 7-day daily forecast for all cities.
   24 variables: min/max/mean temp, precip sum, wind max, sunrise/sunset,
   UV index, sunshine duration, evapotranspiration.
   Dedup key: (city, country_code, date).

3. **air_quality**: 24h hourly air quality for all cities.
   20 variables: PM2.5, PM10, O3, NO2, CO, SO2, dust, US/EU AQI indices.
   Dedup key: (city, country_code, time).

Open-Meteo free tier: 10,000 calls/day, 600 calls/min.
~6,000 cities in batches of 50 = 120 batches x 2 endpoints = 240 calls.
At 4 extractions/day = 960 calls/day (well within limits).

All data is CC-BY-4.0 licensed from Open-Meteo (national weather services).
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import duckdb

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.DEBUG if os.environ.get("DRY_RUN") else logging.INFO,
)
log = logging.getLogger(__name__)

CITIES_URL = (
    "https://raw.githubusercontent.com/tabaqatdev/gdelt-cng/"
    "refs/heads/main/data_helpers/world_cities.parquet"
)
MIN_POPULATION = 100_000

WEATHER_BASE = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_BASE = "https://air-quality-api.open-meteo.com/v1/air-quality"

OUT = os.environ.get("OUTPUT_DIR", "output")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# Hourly weather variables (rich selection for maximum insight)
HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "snow_depth",
    "weather_code",
    "pressure_msl",
    "surface_pressure",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "visibility",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "uv_index",
    "uv_index_clear_sky",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "sunshine_duration",
    "is_day",
    "cape",
    "soil_temperature_0cm",
    "soil_moisture_0_to_1cm",
]

# Daily weather variables
DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "apparent_temperature_mean",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "snowfall_sum",
    "precipitation_hours",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
    "weather_code",
    "sunrise",
    "sunset",
    "daylight_duration",
    "sunshine_duration",
    "uv_index_max",
    "uv_index_clear_sky_max",
]

# Air quality variables
AQ_VARS = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
    "dust",
    "uv_index",
    "uv_index_clear_sky",
    "us_aqi",
    "us_aqi_pm2_5",
    "us_aqi_pm10",
    "us_aqi_nitrogen_dioxide",
    "us_aqi_ozone",
    "european_aqi",
    "european_aqi_pm2_5",
    "european_aqi_pm10",
    "european_aqi_nitrogen_dioxide",
    "european_aqi_ozone",
    "european_aqi_sulphur_dioxide",
]

BATCH_SIZE = 50


def setup(db):
    """Load required extensions."""
    db.execute("INSTALL spatial; LOAD spatial;")
    db.execute("INSTALL httpfs; LOAD httpfs;")
    db.execute("SET geometry_always_xy = true;")


def load_cities(db):
    """Load world cities from remote parquet, filtered by population.

    Returns list of (city, country_code, lat, lon, population) tuples.
    """
    log.info("Loading cities with population >= %s...", f"{MIN_POPULATION:,}")
    rows = db.execute(f"""
        SELECT city, country_code, lat, lon, population
        FROM read_parquet('{CITIES_URL}')
        WHERE population >= {MIN_POPULATION}
          AND lat IS NOT NULL
          AND lon IS NOT NULL
          AND city IS NOT NULL
        ORDER BY population DESC
    """).fetchall()
    countries = len(set(r[1] for r in rows))
    log.info("Loaded %d cities across %d countries", len(rows), countries)
    return rows


def load_dry_run_cities(db):
    """Load a small subset of cities for dry-run validation."""
    log.info("Loading top 50 cities for dry run...")
    rows = db.execute(f"""
        SELECT city, country_code, lat, lon, population
        FROM read_parquet('{CITIES_URL}')
        WHERE population >= 1000000
          AND lat IS NOT NULL
          AND lon IS NOT NULL
          AND city IS NOT NULL
        ORDER BY population DESC
        LIMIT 50
    """).fetchall()
    log.info("Loaded %d cities for dry run", len(rows))
    return rows


def fetch_json(url, retries=5, delay=2.0):
    """Fetch JSON from URL with retry logic and rate-limit awareness."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "ai-data-registry/openmeteo")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = delay * (2 ** attempt)
                log.warning("Rate limited (429), waiting %.0fs (attempt %d/%d)", wait, attempt + 1, retries)
                time.sleep(wait)
            elif attempt < retries - 1:
                log.warning("Retry %d/%d after HTTP %d: %s", attempt + 1, retries, e.code, e)
                time.sleep(delay * (attempt + 1))
            else:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                wait = delay * (attempt + 1)
                log.warning("Retry %d/%d after error: %s (waiting %.0fs)", attempt + 1, retries, e, wait)
                time.sleep(wait)
            else:
                raise


def build_weather_url(lats, lons):
    """Build Open-Meteo forecast URL for a batch of coordinates."""
    lat_str = ",".join(f"{lat:.4f}" for lat in lats)
    lon_str = ",".join(f"{lon:.4f}" for lon in lons)
    hourly = ",".join(HOURLY_VARS)
    daily = ",".join(DAILY_VARS)
    return (
        f"{WEATHER_BASE}?"
        f"latitude={lat_str}&longitude={lon_str}"
        f"&hourly={hourly}&daily={daily}"
        f"&timezone=UTC&forecast_days=7"
        f"&forecast_hours=24"
    )


def build_aq_url(lats, lons):
    """Build Open-Meteo air quality URL for a batch of coordinates."""
    lat_str = ",".join(f"{lat:.4f}" for lat in lats)
    lon_str = ",".join(f"{lon:.4f}" for lon in lons)
    hourly = ",".join(AQ_VARS)
    return (
        f"{AIR_QUALITY_BASE}?"
        f"latitude={lat_str}&longitude={lon_str}"
        f"&hourly={hourly}&forecast_days=1"
        f"&forecast_hours=24"
    )


def create_tables(db):
    """Create empty tables with the correct schema."""
    hourly_cols = ", ".join(f'"{v}" DOUBLE' for v in HOURLY_VARS)
    db.execute(f"""
        CREATE OR REPLACE TABLE weather_hourly (
            city VARCHAR,
            country_code VARCHAR,
            population INTEGER,
            latitude DOUBLE,
            longitude DOUBLE,
            elevation DOUBLE,
            "time" TIMESTAMP,
            snapshot_time TIMESTAMP,
            {hourly_cols},
            geometry GEOMETRY
        )
    """)

    daily_cols = []
    for v in DAILY_VARS:
        if v in ("sunrise", "sunset"):
            daily_cols.append(f'"{v}" VARCHAR')
        else:
            daily_cols.append(f'"{v}" DOUBLE')
    db.execute(f"""
        CREATE OR REPLACE TABLE weather_daily (
            city VARCHAR,
            country_code VARCHAR,
            population INTEGER,
            latitude DOUBLE,
            longitude DOUBLE,
            elevation DOUBLE,
            "date" DATE,
            snapshot_time TIMESTAMP,
            {", ".join(daily_cols)},
            geometry GEOMETRY
        )
    """)

    aq_cols = ", ".join(f'"{v}" DOUBLE' for v in AQ_VARS)
    db.execute(f"""
        CREATE OR REPLACE TABLE air_quality (
            city VARCHAR,
            country_code VARCHAR,
            population INTEGER,
            latitude DOUBLE,
            longitude DOUBLE,
            "time" TIMESTAMP,
            snapshot_time TIMESTAMP,
            {aq_cols},
            geometry GEOMETRY
        )
    """)


def extract_weather(db, cities):
    """Fetch weather data for all cities in batches."""
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("Extracting weather for %d cities at %s...", len(cities), snapshot)

    for batch_start in range(0, len(cities), BATCH_SIZE):
        batch = cities[batch_start:batch_start + BATCH_SIZE]
        names = [c[0] for c in batch]
        codes = [c[1] for c in batch]
        lats = [c[2] for c in batch]
        lons = [c[3] for c in batch]
        pops = [c[4] for c in batch]

        url = build_weather_url(lats, lons)
        try:
            data = fetch_json(url)
        except Exception as e:
            log.error("Weather batch %d failed: %s", batch_start, e)
            continue

        # API returns list for multi-coordinate, dict for single
        if isinstance(data, dict):
            data = [data]

        for i, result in enumerate(data):
            if "error" in result:
                continue
            city_name = names[i]
            country = codes[i]
            pop = pops[i]
            lat = result.get("latitude", lats[i])
            lon = result.get("longitude", lons[i])
            elev = result.get("elevation", 0.0)

            # Hourly data
            hourly = result.get("hourly", {})
            times = hourly.get("time", [])
            if times:
                rows = []
                for t_idx, ts in enumerate(times):
                    row = [city_name, country, pop, lat, lon, elev, ts, snapshot]
                    for var in HOURLY_VARS:
                        vals = hourly.get(var, [])
                        row.append(vals[t_idx] if t_idx < len(vals) else None)
                    rows.append(row + [lon, lat])

                db.executemany(
                    f"""INSERT INTO weather_hourly VALUES (
                        ?, ?, ?, ?, ?, ?, ?::TIMESTAMP, ?::TIMESTAMP,
                        {', '.join('?' for _ in HOURLY_VARS)},
                        ST_Point(?, ?)
                    )""",
                    rows,
                )

            # Daily data
            daily = result.get("daily", {})
            dates = daily.get("time", [])
            if dates:
                rows = []
                for d_idx, ds in enumerate(dates):
                    row = [city_name, country, pop, lat, lon, elev, ds, snapshot]
                    for var in DAILY_VARS:
                        vals = daily.get(var, [])
                        row.append(vals[d_idx] if d_idx < len(vals) else None)
                    rows.append(row + [lon, lat])

                db.executemany(
                    f"""INSERT INTO weather_daily VALUES (
                        ?, ?, ?, ?, ?, ?, ?::DATE, ?::TIMESTAMP,
                        {', '.join('?' for _ in DAILY_VARS)},
                        ST_Point(?, ?)
                    )""",
                    rows,
                )

        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(cities) + BATCH_SIZE - 1) // BATCH_SIZE
        done = min(batch_start + BATCH_SIZE, len(cities))
        if batch_num % 10 == 0 or batch_num == total_batches:
            log.info("Weather: %d/%d cities (batch %d/%d)", done, len(cities), batch_num, total_batches)
        else:
            log.debug("Weather: %d/%d cities (batch %d/%d)", done, len(cities), batch_num, total_batches)
        if batch_start + BATCH_SIZE < len(cities):
            time.sleep(0.6)

    h = db.execute("SELECT COUNT(*) FROM weather_hourly").fetchone()[0]
    d = db.execute("SELECT COUNT(*) FROM weather_daily").fetchone()[0]
    log.info("Weather totals: hourly=%d, daily=%d", h, d)
    return h, d


def extract_air_quality(db, cities):
    """Fetch air quality data for all cities in batches."""
    snapshot = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("Extracting air quality for %d cities...", len(cities))

    for batch_start in range(0, len(cities), BATCH_SIZE):
        batch = cities[batch_start:batch_start + BATCH_SIZE]
        names = [c[0] for c in batch]
        codes = [c[1] for c in batch]
        lats = [c[2] for c in batch]
        lons = [c[3] for c in batch]
        pops = [c[4] for c in batch]

        url = build_aq_url(lats, lons)
        try:
            data = fetch_json(url)
        except Exception as e:
            log.warning("Air quality batch %d failed (non-critical): %s", batch_start, e)
            continue

        if isinstance(data, dict):
            data = [data]

        for i, result in enumerate(data):
            if "error" in result:
                continue
            city_name = names[i]
            country = codes[i]
            pop = pops[i]
            lat = result.get("latitude", lats[i])
            lon = result.get("longitude", lons[i])

            hourly = result.get("hourly", {})
            times = hourly.get("time", [])
            if times:
                rows = []
                for t_idx, ts in enumerate(times):
                    row = [city_name, country, pop, lat, lon, ts, snapshot]
                    for var in AQ_VARS:
                        vals = hourly.get(var, [])
                        row.append(vals[t_idx] if t_idx < len(vals) else None)
                    rows.append(row + [lon, lat])

                db.executemany(
                    f"""INSERT INTO air_quality VALUES (
                        ?, ?, ?, ?, ?, ?::TIMESTAMP, ?::TIMESTAMP,
                        {', '.join('?' for _ in AQ_VARS)},
                        ST_Point(?, ?)
                    )""",
                    rows,
                )

        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(cities) + BATCH_SIZE - 1) // BATCH_SIZE
        done = min(batch_start + BATCH_SIZE, len(cities))
        if batch_num % 10 == 0 or batch_num == total_batches:
            log.info("Air quality: %d/%d cities (batch %d/%d)", done, len(cities), batch_num, total_batches)
        else:
            log.debug("Air quality: %d/%d cities (batch %d/%d)", done, len(cities), batch_num, total_batches)
        if batch_start + BATCH_SIZE < len(cities):
            time.sleep(0.6)

    count = db.execute("SELECT COUNT(*) FROM air_quality").fetchone()[0]
    log.info("Air quality total: %d", count)
    return count


def write_parquet(db, table, order_clause, out_path, geoparquet=True):
    """Write a table to Parquet with standard settings."""
    count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if count == 0:
        log.warning("No data in %s, skipping", table)
        return 0

    geo_opt = ",\n            GEOPARQUET_VERSION 'BOTH'" if geoparquet else ""
    db.execute(f"""
        COPY (
            SELECT * FROM {table}
            ORDER BY {order_clause}
        ) TO '{out_path}' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            COMPRESSION_LEVEL 15,
            ROW_GROUP_SIZE 100000{geo_opt}
        )
    """)
    log.info("Wrote %s (%d rows)", out_path, count)
    return count


def main():
    t0 = time.monotonic()
    db = duckdb.connect()
    setup(db)
    create_tables(db)

    if DRY_RUN:
        cities = load_dry_run_cities(db)
    else:
        cities = load_cities(db)

    extract_weather(db, cities)
    extract_air_quality(db, cities)

    os.makedirs(OUT, exist_ok=True)
    h = write_parquet(
        db, "weather_hourly",
        'ST_Hilbert(geometry), "time"',
        f"{OUT}/weather_hourly.parquet",
    )
    d = write_parquet(
        db, "weather_daily",
        'ST_Hilbert(geometry), "date"',
        f"{OUT}/weather_daily.parquet",
    )
    a = write_parquet(
        db, "air_quality",
        'ST_Hilbert(geometry), "time"',
        f"{OUT}/air_quality.parquet",
    )
    db.close()

    elapsed = time.monotonic() - t0
    label = "Dry run" if DRY_RUN else "Extract"
    log.info(
        "%s complete: weather_hourly=%d, weather_daily=%d, air_quality=%d (%.1fs)",
        label, h, d, a, elapsed,
    )


if __name__ == "__main__":
    main()

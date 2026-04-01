"""Extract flight data from the OpenSky Network API.

Two data streams, each producing a separate GeoParquet file:

1. **states** (`/api/states/all`): Real-time aircraft position snapshots.
   ~10k rows per call. Each aircraft appears once per snapshot_time.
   Dedup key: (icao24, snapshot_time). No overlap between hourly runs.

2. **flights** (`/api/flights/all?begin=...&end=...`): Completed flights
   with estimated departure/arrival airports. 2-hour lookback window.
   Overlaps between consecutive hourly runs, so dedup is essential.
   Dedup key: (icao24, first_seen). Same flight returned by consecutive
   calls gets the same (icao24, first_seen) pair.

DuckLake partitioning: day(snapshot_time), hour(snapshot_time) for states.
DuckLake partitioning: day(last_seen) for flights.

All timestamps converted from Unix epoch seconds via make_timestamp().
GeoParquet written with GEOPARQUET_VERSION 'BOTH' for DuckLake zero-copy.
"""

import logging
import os
import time

import duckdb

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.DEBUG if os.environ.get("DRY_RUN") else logging.INFO,
)
log = logging.getLogger(__name__)

STATES_URL = "https://opensky-network.org/api/states/all"
FLIGHTS_URL = "https://opensky-network.org/api/flights/all"
OUT = os.environ.get("OUTPUT_DIR", "output")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


def setup(db):
    """Load required extensions."""
    db.execute("INSTALL spatial; LOAD spatial;")
    db.execute("INSTALL httpfs; LOAD httpfs;")
    db.execute("SET geometry_always_xy = true;")


def extract_states(db):
    """Fetch real-time aircraft positions from /api/states/all.

    The API returns a JSON object with:
      - "time": Unix epoch seconds (snapshot timestamp)
      - "states": array of arrays, each with 17 positional fields

    Anonymous access: ~10k aircraft globally, 10s update interval.
    """
    log.info("Fetching live state vectors from OpenSky Network...")
    db.execute(f"""
        CREATE OR REPLACE TABLE raw_states AS
        WITH api AS (
            SELECT
                unnest(states) AS sv,
                "time" AS snapshot_ts
            FROM read_json_auto('{STATES_URL}')
        )
        SELECT
            CAST(sv[1]  AS VARCHAR)  AS icao24,
            NULLIF(TRIM(CAST(sv[2] AS VARCHAR)), '') AS callsign,
            CAST(sv[3]  AS VARCHAR)  AS origin_country,
            make_timestamp(CAST(sv[4]  AS BIGINT) * 1000000) AS time_position,
            make_timestamp(CAST(sv[5]  AS BIGINT) * 1000000) AS last_contact,
            CAST(sv[6]  AS DOUBLE)   AS longitude,
            CAST(sv[7]  AS DOUBLE)   AS latitude,
            CAST(sv[8]  AS DOUBLE)   AS baro_altitude,
            CAST(sv[9]  AS BOOLEAN)  AS on_ground,
            CAST(sv[10] AS DOUBLE)   AS velocity,
            CAST(sv[11] AS DOUBLE)   AS true_track,
            CAST(sv[12] AS DOUBLE)   AS vertical_rate,
            CAST(sv[14] AS DOUBLE)   AS geo_altitude,
            CAST(sv[15] AS VARCHAR)  AS squawk,
            CAST(sv[16] AS BOOLEAN)  AS spi,
            CAST(sv[17] AS INTEGER)  AS position_source,
            make_timestamp(snapshot_ts * 1000000) AS snapshot_time,
            ST_Point(CAST(sv[6] AS DOUBLE), CAST(sv[7] AS DOUBLE)) AS geometry
        FROM api
        WHERE sv[6] IS NOT NULL
          AND sv[7] IS NOT NULL
          AND CAST(sv[6] AS VARCHAR) != 'null'
          AND CAST(sv[7] AS VARCHAR) != 'null'
    """)

    count = db.execute("SELECT COUNT(*) FROM raw_states").fetchone()[0]
    snap = db.execute("SELECT MIN(snapshot_time) FROM raw_states").fetchone()[0]
    log.info("States: %d aircraft at %s", count, snap)
    return count


def extract_flights(db):
    """Fetch completed flights from /api/flights/all (last 2 hours).

    Anonymous access returns recently completed flights with estimated
    departure/arrival airports. The 2-hour window is the API maximum.
    Consecutive hourly runs will overlap by ~1 hour, producing duplicates
    for flights that completed in the overlap window.

    Returns 0 if the endpoint fails (non-critical, states is the primary).
    """
    now = int(time.time())
    begin = now - 7200  # 2 hours ago (API maximum window)
    url = f"{FLIGHTS_URL}?begin={begin}&end={now}"

    log.info("Fetching completed flights from OpenSky Network...")
    try:
        db.execute(f"""
            CREATE OR REPLACE TABLE raw_flights AS
            SELECT
                icao24,
                NULLIF(TRIM(callsign), '') AS callsign,
                make_timestamp(firstSeen * 1000000) AS first_seen,
                make_timestamp(lastSeen * 1000000) AS last_seen,
                estDepartureAirport AS departure_airport,
                estArrivalAirport AS arrival_airport,
                estDepartureAirportHorizDistance AS departure_horiz_distance,
                estDepartureAirportVertDistance AS departure_vert_distance,
                estArrivalAirportHorizDistance AS arrival_horiz_distance,
                estArrivalAirportVertDistance AS arrival_vert_distance,
                departureAirportCandidatesCount AS departure_candidates,
                arrivalAirportCandidatesCount AS arrival_candidates
            FROM read_json_auto('{url}')
        """)
        count = db.execute("SELECT COUNT(*) FROM raw_flights").fetchone()[0]
        log.info("Flights: %d completed flights in last 2h", count)
        return count
    except Exception as e:
        log.warning("Flights endpoint failed (non-critical): %s", e)
        db.execute("""
            CREATE OR REPLACE TABLE raw_flights AS
            SELECT
                '' AS icao24, '' AS callsign,
                TIMESTAMP '1970-01-01' AS first_seen,
                TIMESTAMP '1970-01-01' AS last_seen,
                '' AS departure_airport, '' AS arrival_airport,
                0 AS departure_horiz_distance, 0 AS departure_vert_distance,
                0 AS arrival_horiz_distance, 0 AS arrival_vert_distance,
                0 AS departure_candidates, 0 AS arrival_candidates
            WHERE false
        """)
        return 0


def generate_dry_run(db):
    """Generate synthetic data for PR validation."""
    snapshot_ts = int(time.time())
    log.info("Dry run: generating synthetic flight data")

    # Synthetic states
    db.execute(f"""
        CREATE OR REPLACE TABLE raw_states AS
        SELECT
            printf('%06x', i) AS icao24,
            'TST' || printf('%04d', i) AS callsign,
            CASE i % 6
                WHEN 0 THEN 'United States'
                WHEN 1 THEN 'Germany'
                WHEN 2 THEN 'France'
                WHEN 3 THEN 'Japan'
                WHEN 4 THEN 'Brazil'
                ELSE 'Australia'
            END AS origin_country,
            make_timestamp({snapshot_ts}::BIGINT * 1000000) AS time_position,
            make_timestamp({snapshot_ts}::BIGINT * 1000000) AS last_contact,
            -180 + random() * 360 AS longitude,
            -90 + random() * 180 AS latitude,
            random() * 13000 AS baro_altitude,
            (i % 20 = 0) AS on_ground,
            50 + random() * 250 AS velocity,
            random() * 360 AS true_track,
            -10 + random() * 20 AS vertical_rate,
            random() * 13000 AS geo_altitude,
            CASE WHEN i % 3 = 0
                THEN printf('%04d', 1000 + (random() * 6777)::INT)
                ELSE NULL
            END AS squawk,
            false AS spi,
            0 AS position_source,
            make_timestamp({snapshot_ts}::BIGINT * 1000000) AS snapshot_time,
            ST_Point(-180 + random() * 360, -90 + random() * 180) AS geometry
        FROM range(2000) t(i)
    """)

    # Synthetic flights
    db.execute(f"""
        CREATE OR REPLACE TABLE raw_flights AS
        SELECT
            printf('%06x', i) AS icao24,
            'TST' || printf('%04d', i) AS callsign,
            make_timestamp(({snapshot_ts} - 3600 + i * 10)::BIGINT * 1000000) AS first_seen,
            make_timestamp(({snapshot_ts} - 600 + i * 5)::BIGINT * 1000000) AS last_seen,
            CASE i % 4 WHEN 0 THEN 'KJFK' WHEN 1 THEN 'EGLL'
                        WHEN 2 THEN 'LFPG' ELSE 'RJTT' END AS departure_airport,
            CASE i % 4 WHEN 0 THEN 'EGLL' WHEN 1 THEN 'KJFK'
                        WHEN 2 THEN 'EDDF' ELSE 'KLAX' END AS arrival_airport,
            (random() * 5000)::INT AS departure_horiz_distance,
            (random() * 500)::INT AS departure_vert_distance,
            (random() * 5000)::INT AS arrival_horiz_distance,
            (random() * 500)::INT AS arrival_vert_distance,
            (random() * 10)::INT AS departure_candidates,
            (random() * 10)::INT AS arrival_candidates
        FROM range(200) t(i)
    """)

    states_n = db.execute("SELECT COUNT(*) FROM raw_states").fetchone()[0]
    flights_n = db.execute("SELECT COUNT(*) FROM raw_flights").fetchone()[0]
    log.debug("Dry run states: %d, flights: %d", states_n, flights_n)
    return states_n, flights_n


def write_states(db):
    """Write states GeoParquet, Hilbert-sorted for spatial query performance."""
    os.makedirs(OUT, exist_ok=True)
    count = db.execute("SELECT COUNT(*) FROM raw_states").fetchone()[0]

    db.execute(f"""
        COPY (
            SELECT * REPLACE (ST_SetCRS(geometry, 'EPSG:4326') AS geometry)
            FROM raw_states
            ORDER BY ST_Hilbert(geometry)
        ) TO '{OUT}/states.parquet' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            COMPRESSION_LEVEL 15,
            ROW_GROUP_SIZE 100000,
            GEOPARQUET_VERSION 'BOTH'
        )
    """)
    log.info("Wrote %s/states.parquet (%d rows)", OUT, count)
    return count


def write_flights(db):
    """Write flights Parquet (no geometry column, sorted by last_seen)."""
    count = db.execute("SELECT COUNT(*) FROM raw_flights").fetchone()[0]
    if count == 0:
        log.info("No flights to write, skipping")
        return 0

    db.execute(f"""
        COPY (
            SELECT * FROM raw_flights
            ORDER BY last_seen, icao24
        ) TO '{OUT}/flights.parquet' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            COMPRESSION_LEVEL 15,
            ROW_GROUP_SIZE 100000
        )
    """)
    log.info("Wrote %s/flights.parquet (%d rows)", OUT, count)
    return count


def main():
    t0 = time.time()
    db = duckdb.connect()
    setup(db)

    if DRY_RUN:
        generate_dry_run(db)
    else:
        extract_states(db)
        extract_flights(db)

    states_n = write_states(db)
    flights_n = write_flights(db)
    db.close()

    label = "Dry run" if DRY_RUN else "Extract"
    elapsed = time.time() - t0
    log.info("%s complete: %d states, %d flights (%.1fs)", label, states_n, flights_n, elapsed)


if __name__ == "__main__":
    main()

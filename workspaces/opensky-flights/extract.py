"""Extract live flight state vectors from the OpenSky Network API.

Uses native DuckDB HTTP + JSON capabilities (httpfs extension) instead of
external HTTP libraries. Writes Hive-partitioned GeoParquet by date.

Anonymous API: ~5k-15k aircraft per snapshot, 10s rate limit.
Dedup key: (icao24, snapshot_time) for append mode.
"""

import os
import time

import duckdb

API_URL = "https://opensky-network.org/api/states/all"
OUT = os.environ.get("OUTPUT_DIR", "output")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


def extract_live(db):
    """Fetch from OpenSky API using DuckDB httpfs + JSON parsing."""
    # DuckDB reads states as JSON[][] (1-based indexing after unnest)
    db.execute(f"""
        CREATE TABLE raw AS
        WITH api AS (
            SELECT
                unnest(states) AS sv,
                "time" AS snapshot_ts
            FROM read_json_auto('{API_URL}')
        )
        SELECT
            CAST(sv[1]  AS VARCHAR) AS icao24,
            NULLIF(TRIM(CAST(sv[2] AS VARCHAR)), '') AS callsign,
            CAST(sv[3]  AS VARCHAR) AS origin_country,
            CAST(sv[4]  AS BIGINT) AS time_position,
            CAST(sv[5]  AS BIGINT) AS last_contact,
            CAST(sv[6]  AS DOUBLE) AS longitude,
            CAST(sv[7]  AS DOUBLE) AS latitude,
            CAST(sv[8]  AS DOUBLE) AS baro_altitude,
            CAST(sv[9]  AS BOOLEAN) AS on_ground,
            CAST(sv[10] AS DOUBLE) AS velocity,
            CAST(sv[11] AS DOUBLE) AS true_track,
            CAST(sv[12] AS DOUBLE) AS vertical_rate,
            CAST(sv[14] AS DOUBLE) AS geo_altitude,
            CAST(sv[15] AS VARCHAR) AS squawk,
            CAST(sv[16] AS BOOLEAN) AS spi,
            CAST(sv[17] AS INTEGER) AS position_source,
            snapshot_ts
        FROM api
        WHERE sv[6] IS NOT NULL
          AND sv[7] IS NOT NULL
          AND CAST(sv[6] AS VARCHAR) != 'null'
          AND CAST(sv[7] AS VARCHAR) != 'null'
    """)


def generate_dry_run(db):
    """Generate synthetic flight data for PR validation."""
    snapshot_ts = int(time.time())
    db.execute(f"""
        CREATE TABLE raw AS
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
            {snapshot_ts}::BIGINT AS time_position,
            {snapshot_ts}::BIGINT AS last_contact,
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
            {snapshot_ts}::BIGINT AS snapshot_ts
        FROM range(2000) t(i)
    """)


def write_geoparquet(db):
    """Write a single GeoParquet file per run, Hilbert-sorted.

    DuckLake manages file catalog and compaction, so we write one flat file
    per snapshot with snapshot_date as a regular column (not Hive-partitioned).
    This avoids losing the partition column when DuckLake registers files
    via ducklake_add_data_files().
    """
    os.makedirs(OUT, exist_ok=True)

    count = db.execute("SELECT COUNT(*) FROM raw").fetchone()[0]

    db.execute(f"""
        COPY (
            SELECT
                icao24,
                callsign,
                origin_country,
                epoch_ms(time_position * 1000)::TIMESTAMP AS time_position,
                epoch_ms(last_contact * 1000)::TIMESTAMP AS last_contact,
                longitude,
                latitude,
                baro_altitude,
                on_ground,
                velocity,
                true_track,
                vertical_rate,
                geo_altitude,
                squawk,
                spi,
                position_source,
                epoch_ms(snapshot_ts * 1000)::TIMESTAMP AS snapshot_time,
                CAST(epoch_ms(snapshot_ts * 1000) AS DATE) AS snapshot_date,
                ST_Point(longitude, latitude) AS geometry
            FROM raw
            ORDER BY ST_Hilbert(ST_Point(longitude, latitude))
        ) TO '{OUT}/states.parquet' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            COMPRESSION_LEVEL 15,
            ROW_GROUP_SIZE 100000
        )
    """)

    return count


def main():
    db = duckdb.connect()
    db.execute("INSTALL spatial; LOAD spatial;")
    db.execute("INSTALL httpfs; LOAD httpfs;")

    if DRY_RUN:
        print("Dry run: generating synthetic flight data")
        generate_dry_run(db)
    else:
        print("Fetching live flight data from OpenSky Network...")
        extract_live(db)

    count = write_geoparquet(db)
    db.close()

    label = "Dry run" if DRY_RUN else "Extract"
    print(f"{label}: wrote {OUT}/states.parquet ({count} rows)")


if __name__ == "__main__":
    main()

"""Seed the KMG-shaped synthetic warehouse.

Runs against ADMIN_URL: ddh and stg are owned by the superuser, and neither
warehouse_ro (SELECT only) nor app_rw (app schema only) can write here.

The data is deliberately imperfect. Legacy integer status codes, null net
gains, orphan well_ids and inconsistent contractor casing are the mess a
real warehouse has, and they are what the layer's `confidence: low`
annotations and the deferred profiler exist to deal with.
"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta

import psycopg

SEED = 42
N_WELLS = 200
N_INTERVENTIONS = 4_000
PRODUCTION_START = date(2024, 8, 1)
PRODUCTION_END = date(2026, 8, 1)
INTERVENTION_START = date(2024, 1, 1)
INTERVENTION_END = date(2026, 8, 1)

REGIONS = ["Mangystau", "Atyrau", "Aktobe", "Kyzylorda", "West Kazakhstan"]

# Approximate centroids of the real oblasts, in degrees. Wells are scattered
# around these rather than placed on surveyed coordinates: enough spatial
# structure that a map shows real clustering by region, and no pretence that
# any individual point is where a well actually is.
REGION_CENTRES = {
    "Mangystau":       (43.7, 52.9),
    "Atyrau":          (47.1, 51.9),
    "Aktobe":          (49.5, 57.2),
    "Kyzylorda":       (44.9, 65.5),
    "West Kazakhstan": (50.5, 51.4),
}
REGION_SPREAD = 1.1     # degrees, roughly an oblast-sized scatter
FIELDS = ["Uzen", "Zhetybai", "Karazhanbas", "Kalamkas", "Zhanazhol", "Kumkol"]
WELL_TYPES = ["PRODUCER", "INJECTOR", "OBSERVATION"]
INTERVENTION_TYPES = ["FRAC", "WORKOVER", "ACIDIZING", "PERFORATION"]
STATUSES = ["COMPLETED", "CANCELLED", "IN_PROGRESS"]

# Same three contractors, spelled four ways. Real warehouses look like this.
CONTRACTORS = [
    "KazMunayService", "kazmunayservice", "KAZMUNAYSERVICE", "KazMunay Service",
    "Schlumberger", "schlumberger", "SLB",
    "Halliburton", "HALLIBURTON",
]


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days))


def main() -> None:
    rng = random.Random(SEED)
    dsn = os.environ["ADMIN_URL"]

    wells = []
    for well_id in range(1, N_WELLS + 1):
        region = rng.choice(REGIONS)
        lat0, lon0 = REGION_CENTRES[region]
        wells.append((
            well_id,
            f"KMG-{well_id:04d}",
            region,
            rng.choice(FIELDS),
            random_date(rng, date(1998, 1, 1), date(2023, 12, 31)),
            rng.choices(WELL_TYPES, weights=[80, 15, 5])[0],
            round(rng.gauss(lat0, REGION_SPREAD / 2), 6),
            round(rng.gauss(lon0, REGION_SPREAD / 2), 6),
        ))

    # A handful of interventions point at wells that do not exist. This is
    # why the fact tables carry no FK constraint.
    orphan_ids = [9001, 9002, 9003]

    interventions = []
    for job_id in range(1, N_INTERVENTIONS + 1):
        well_id = rng.randint(1, N_WELLS)
        if rng.random() < 0.002:
            well_id = rng.choice(orphan_ids)

        itype = rng.choice(INTERVENTION_TYPES)

        # ~1% of rows still carry the pre-migration integer status codes.
        if rng.random() < 0.01:
            status = rng.choice(["1", "2", "3"])
        else:
            status = rng.choices(STATUSES, weights=[75, 12, 13])[0]

        # ~3% of net gains were never backfilled.
        if rng.random() < 0.03:
            net_gain = None
        else:
            base = {"FRAC": 3200, "WORKOVER": 1400,
                    "ACIDIZING": 900, "PERFORATION": 1800}[itype]
            net_gain = round(rng.gauss(base, base * 0.35), 2)

        interventions.append((
            job_id,
            well_id,
            random_date(rng, INTERVENTION_START, INTERVENTION_END),
            itype,
            status,
            net_gain,
            round(rng.gauss(250_000, 90_000), 2),
            rng.choice(CONTRACTORS),
        ))

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE ddh.dim_wells, ddh.fct_well_interventions, "
                    "ddh.fct_production_daily, stg.wells_raw, "
                    "stg.interventions_raw")

        with cur.copy("COPY ddh.dim_wells (well_id, well_name, region_name, "
                      "field_name, spud_date, well_type, latitude, longitude) "
                      "FROM STDIN") as copy:
            for row in wells:
                copy.write_row(row)

        with cur.copy("COPY ddh.fct_well_interventions (intervention_id, "
                      "well_id, intervention_date, intervention_type, status, "
                      "net_gain_bbl, cost_usd, contractor) FROM STDIN") as copy:
            for row in interventions:
                copy.write_row(row)

        n_days = (PRODUCTION_END - PRODUCTION_START).days
        n_prod = 0
        with cur.copy("COPY ddh.fct_production_daily (well_id, reading_date, "
                      "oil_bbl, gas_mcf, water_bbl, downtime_hours) "
                      "FROM STDIN") as copy:
            # Indexed rather than unpacked: the row grew two coordinate
            # columns and a positional unpack is the kind of thing that
            # breaks silently the next time it grows.
            for well in wells:
                well_id, well_type = well[0], well[5]
                if well_type != "PRODUCER":
                    continue
                peak = rng.uniform(40, 900)
                decline = rng.uniform(0.00015, 0.0009)
                for offset in range(n_days):
                    reading = PRODUCTION_START + timedelta(days=offset)
                    rate = peak * (1 - decline) ** offset
                    down = round(rng.uniform(0, 24), 2) if rng.random() < 0.04 else 0.0
                    factor = max(0.0, 1 - down / 24)
                    copy.write_row((
                        well_id,
                        reading,
                        round(max(0.0, rng.gauss(rate, rate * 0.08)) * factor, 2),
                        round(max(0.0, rng.gauss(rate * 4.2, rate * 0.4)) * factor, 2),
                        round(max(0.0, rng.gauss(rate * 1.6, rate * 0.3)) * factor, 2),
                        down,
                    ))
                    n_prod += 1

        # stg mirrors: same facts, raw source shapes, everything text.
        with cur.copy("COPY stg.wells_raw (well_id, wellname, region, "
                      "fieldname, spud_dt, welltype) FROM STDIN") as copy:
            # The raw landing table predates the coordinates and stays as the
            # source system sends it -- six columns, all text.
            for w in wells:
                copy.write_row(tuple(None if v is None else str(v)
                                     for v in w[:6]))

        with cur.copy("COPY stg.interventions_raw (job_id, well_id, job_dt, "
                      "job_type, stat, gain, cost, contractor) "
                      "FROM STDIN") as copy:
            for i in interventions:
                copy.write_row(tuple(None if v is None else str(v) for v in i))

        cur.execute("ANALYZE")

    print(f"seeded: {len(wells)} wells, {len(interventions)} interventions, "
          f"{n_prod} production rows")


if __name__ == "__main__":
    main()

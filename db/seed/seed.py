"""Seed the synthetic oil-and-gas warehouse.

Runs against ADMIN_URL: ddh and stg are owned by the superuser, and neither
warehouse_ro (SELECT only) nor app_rw (app schema only) can write here.

The data is deliberately imperfect. Legacy integer status codes, null net
gains, orphan well_ids and inconsistent contractor casing remain visible in
staging. The reviewed curated mapping is 1=COMPLETED, 2=CANCELLED and
3=IN_PROGRESS; manager queries read only those curated values.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import random
import sys
import time
from datetime import date, datetime, timedelta
from typing import Iterator, Literal, Sequence

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
    "NorthfieldService", "northfieldservice", "NORTHFIELDSERVICE", "Northfield Service",
    "Schlumberger", "schlumberger", "SLB",
    "Halliburton", "HALLIBURTON",
]

REALISTIC_PRODUCTION_END = date(2026, 8, 1)
REALISTIC_WELLS = 600
REALISTIC_PRODUCERS = 480
REALISTIC_INJECTORS = 90
REALISTIC_OBSERVATION = 30
REALISTIC_INTERVENTIONS = 16_000
REALISTIC_DOWNTIME_EVENTS = 12_000
REALISTIC_DB = "semantic_realistic"

# Synthetic operating catalogue.  Names are plausible enough to make a
# manager-facing dashboard legible, but are not intended to describe actual
# ownership or field boundaries.
FIELD_CATALOG = (
    ("Uzen", "Mangystau", "Mangystau South", "Northfield Operating", "Mangystau"),
    ("Zhetybai", "Mangystau", "Mangystau South", "Northfield Operating", "Mangystau"),
    ("Karazhanbas", "Mangystau", "Buzachi", "Buzachi Energy", "North Caspian"),
    ("Kalamkas", "Mangystau", "Buzachi", "Northfield Operating", "North Caspian"),
    ("Dunga", "Mangystau", "Coastal", "Caspian Operations", "Mangystau"),
    ("North Buzachi", "Mangystau", "Buzachi", "Buzachi Energy", "North Caspian"),
    ("Tengiz", "Atyrau", "Atyrau Core", "Caspian Operations", "Pre-Caspian"),
    ("Kashagan", "Atyrau", "Offshore", "North Caspian Co", "North Caspian"),
    ("Dossor", "Atyrau", "Atyrau Mature", "Northfield Operating", "Pre-Caspian"),
    ("Makat", "Atyrau", "Atyrau Mature", "Northfield Operating", "Pre-Caspian"),
    ("Kairan", "Atyrau", "Offshore", "North Caspian Co", "North Caspian"),
    ("Altyguyi", "Atyrau", "Atyrau Core", "Caspian Operations", "Pre-Caspian"),
    ("Zhanazhol", "Aktobe", "Aktobe East", "Aktobe Petroleum", "Pre-Caspian"),
    ("Kenkiyak", "Aktobe", "Aktobe West", "Aktobe Petroleum", "Pre-Caspian"),
    ("Alibekmola", "Aktobe", "Aktobe East", "Northfield Operating", "Pre-Caspian"),
    ("Kozhasai", "Aktobe", "Aktobe West", "Northfield Operating", "Pre-Caspian"),
    ("Urikhtau", "Aktobe", "Aktobe East", "Aktobe Petroleum", "Pre-Caspian"),
    ("Kumkol", "Kyzylorda", "Turgai", "Turgai Energy", "South Turgai"),
    ("Akshabulak", "Kyzylorda", "Turgai", "Turgai Energy", "South Turgai"),
    ("Kyzylkiya", "Kyzylorda", "Turgai", "Northfield Operating", "South Turgai"),
    ("Aryskum", "Kyzylorda", "Turgai", "Turgai Energy", "South Turgai"),
    ("Chinarevskoye", "West Kazakhstan", "Oral", "WestKaz Energy", "Pre-Caspian"),
    ("Rozhkovskoye", "West Kazakhstan", "Oral", "WestKaz Energy", "Pre-Caspian"),
    ("Karachaganak", "West Kazakhstan", "Karachaganak", "Caspian Operations", "Pre-Caspian"),
)


@dataclass(frozen=True)
class SeedConfig:
    name: Literal["demo", "realistic"]
    seed: int
    production_start: date
    production_end: date
    well_count: int
    producer_count: int
    injector_count: int
    observation_count: int
    field_count: int
    intervention_count: int
    downtime_event_count: int
    intervention_start: date
    intervention_end: date

    @property
    def production_days(self) -> int:
        return (self.production_end - self.production_start).days

    @property
    def production_row_count(self) -> int:
        return self.producer_count * self.production_days

    @property
    def month_count(self) -> int:
        last_included = self.production_end - timedelta(days=1)
        return (
            (last_included.year - self.production_start.year) * 12
            + last_included.month - self.production_start.month
            + 1
        )

    @property
    def target_row_count(self) -> int:
        return self.field_count * self.month_count


@dataclass(frozen=True)
class SeedCommand:
    config: SeedConfig
    confirmation: str


@dataclass(frozen=True)
class Well:
    well_id: int
    well_name: str
    region_name: str
    field_name: str
    spud_date: date
    well_type: str
    latitude: float
    longitude: float
    asset_name: str
    operator_name: str
    basin_name: str
    operating_status: str
    lift_method: str

    def curated_row(self) -> tuple[object, ...]:
        return (
            self.well_id, self.well_name, self.region_name, self.field_name,
            self.spud_date, self.well_type, self.latitude, self.longitude,
            self.asset_name, self.operator_name, self.basin_name,
            self.operating_status, self.lift_method,
        )


@dataclass(frozen=True)
class DowntimeEvent:
    event_id: int
    well_id: int
    event_start: datetime
    event_end: datetime
    duration_hours: float
    downtime_category: str
    planning_status: str

    def curated_row(self) -> tuple[object, ...]:
        return (
            self.event_id, self.well_id, self.event_start, self.event_end,
            self.duration_hours, self.downtime_category, self.planning_status,
        )


def _years_before(value: date, years: int) -> date:
    """Return the same calendar day ``years`` earlier, including leap days."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        # 29 February has no counterpart in a non-leap year.  This branch is
        # kept for general CLI inputs even though an eight-year shift preserves
        # leap-year alignment for all currently supported dates.
        return value.replace(year=value.year - years, day=28)


def build_seed_config(
    profile: Literal["demo", "realistic"], end_date: date | None = None
) -> SeedConfig:
    if profile == "demo":
        end = end_date or PRODUCTION_END
        start = _years_before(end, 2) if end_date else PRODUCTION_START
        return SeedConfig(
            name="demo", seed=SEED,
            production_start=start, production_end=end,
            well_count=N_WELLS, producer_count=162, injector_count=28,
            observation_count=10, field_count=len(FIELDS),
            intervention_count=N_INTERVENTIONS, downtime_event_count=2_000,
            intervention_start=INTERVENTION_START,
            intervention_end=INTERVENTION_END if end_date is None else end,
        )
    end = end_date or REALISTIC_PRODUCTION_END
    return SeedConfig(
        name="realistic", seed=SEED,
        production_start=_years_before(end, 8), production_end=end,
        well_count=REALISTIC_WELLS, producer_count=REALISTIC_PRODUCERS,
        injector_count=REALISTIC_INJECTORS,
        observation_count=REALISTIC_OBSERVATION,
        field_count=len(FIELD_CATALOG),
        intervention_count=REALISTIC_INTERVENTIONS,
        downtime_event_count=REALISTIC_DOWNTIME_EVENTS,
        intervention_start=_years_before(end, 8),
        intervention_end=end,
    )


def parse_seed_command(argv: Sequence[str] | None = None) -> SeedCommand:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("demo", "realistic"), default="demo")
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    return SeedCommand(
        config=build_seed_config(args.profile, args.end_date),
        confirmation=args.confirm,
    )


def parse_seed_config(argv: Sequence[str] | None = None) -> SeedConfig:
    """Parse generation settings without treating helper use as CLI access."""
    return parse_seed_command(argv).config


def require_realistic_cli_access(dsn: str, confirmation: str) -> None:
    """Authorize the destructive realistic CLI path before it can seed."""
    if confirmation != REALISTIC_DB:
        raise ValueError(
            "realistic seeding is opt-in; rerun with CONFIRM=semantic_realistic"
        )
    with psycopg.connect(dsn) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
    if database != REALISTIC_DB:
        raise ValueError(
            f"realistic seeding must target database {REALISTIC_DB}; got {database}"
        )


def _well_attributes(field_name: str, well_id: int, well_type: str) -> tuple[str, ...]:
    match = next((row for row in FIELD_CATALOG if row[0] == field_name), None)
    if match is None:
        # The six-field demo predates the catalogue.  Keep its field choices
        # unchanged while assigning stable, synthetic enrichment values.
        asset_name = f"Demo Asset {(FIELDS.index(field_name) // 2) + 1}"
        operator_name = "Northfield Operating"
        basin_name = "Synthetic Basin"
    else:
        _, _, asset_name, operator_name, basin_name = match
    if well_type == "OBSERVATION":
        operating_status = "MONITORING"
        lift_method = "NONE"
    elif well_id % 29 == 0:
        operating_status = "SHUT_IN"
        lift_method = "NONE" if well_type != "PRODUCER" else "ESP"
    elif well_type == "INJECTOR":
        operating_status = "ACTIVE"
        lift_method = "NONE"
    else:
        operating_status = "ACTIVE"
        lift_method = ("ESP", "GAS_LIFT", "ROD_PUMP", "NATURAL_FLOW")[well_id % 4]
    return asset_name, operator_name, basin_name, operating_status, lift_method


def generate_wells(
    config: SeedConfig, rng: random.Random | None = None
) -> list[Well]:
    rng = rng or random.Random(config.seed)
    wells: list[Well] = []
    if config.name == "demo":
        # Keep the historical RNG call order byte-for-byte: several screenshots
        # and golden query fixtures depend on the familiar demo distribution.
        for well_id in range(1, config.well_count + 1):
            region = rng.choice(REGIONS)
            field_name = rng.choice(FIELDS)
            spud = random_date(rng, date(1998, 1, 1), date(2023, 12, 31))
            well_type = rng.choices(WELL_TYPES, weights=[80, 15, 5])[0]
            lat0, lon0 = REGION_CENTRES[region]
            lat = round(rng.gauss(lat0, REGION_SPREAD / 2), 6)
            lon = round(rng.gauss(lon0, REGION_SPREAD / 2), 6)
            attributes = _well_attributes(field_name, well_id, well_type)
            wells.append(Well(
                well_id, f"NF-{well_id:04d}", region, field_name, spud,
                well_type, lat, lon, *attributes,
            ))
        return wells
    else:
        assignments = []
        for field_index, (field_name, region, *_rest) in enumerate(FIELD_CATALOG):
            types = (["PRODUCER"] * 20
                     + ["INJECTOR"] * (4 if field_index < 18 else 3)
                     + ["OBSERVATION"] * (1 if field_index < 18 else 2))
            assignments.extend((region, field_name, well_type) for well_type in types)

    for well_id, (region, field_name, well_type) in enumerate(assignments, start=1):
        lat0, lon0 = REGION_CENTRES[region]
        spud_end = (
            config.production_start + timedelta(days=1)
            if well_type == "PRODUCER"
            else date(2023, 12, 31)
        )
        spud = random_date(rng, date(1998, 1, 1), spud_end)
        lat = round(rng.gauss(lat0, REGION_SPREAD / 2), 6)
        lon = round(rng.gauss(lon0, REGION_SPREAD / 2), 6)
        attributes = _well_attributes(field_name, well_id, well_type)
        wells.append(Well(
            well_id=well_id,
            well_name=f"NF-{well_id:04d}",
            region_name=region,
            field_name=field_name,
            spud_date=spud,
            well_type=well_type,
            latitude=lat,
            longitude=lon,
            asset_name=attributes[0],
            operator_name=attributes[1],
            basin_name=attributes[2],
            operating_status=attributes[3],
            lift_method=attributes[4],
        ))
    return wells


def iter_production_rows(
    config: SeedConfig,
    wells: Sequence[Well],
    rng: random.Random | None = None,
) -> Iterator[tuple[object, ...]]:
    rng = rng or random.Random(config.seed + 1_000)
    for well in wells:
        if well.well_type != "PRODUCER":
            continue
        peak = rng.uniform(40, 900)
        decline = rng.uniform(0.00015, 0.0009)
        for offset in range(config.production_days):
            reading = config.production_start + timedelta(days=offset)
            rate = peak * (1 - decline) ** offset
            down = round(rng.uniform(0, 24), 2) if rng.random() < 0.04 else 0.0
            factor = max(0.0, 1 - down / 24)
            yield (
                well.well_id,
                reading,
                round(max(0.0, rng.gauss(rate, rate * 0.08)) * factor, 2),
                round(max(0.0, rng.gauss(rate * 4.2, rate * 0.4)) * factor, 2),
                round(max(0.0, rng.gauss(rate * 1.6, rate * 0.3)) * factor, 2),
                down,
            )


def curated_status(value: str) -> str:
    """Map reviewed legacy codes while leaving the raw landing value intact."""
    normalized = value.upper()
    return {
        "1": "COMPLETED",
        "2": "CANCELLED",
        "3": "IN_PROGRESS",
    }.get(normalized, normalized)


@dataclass(frozen=True)
class Intervention:
    intervention_id: int
    well_id: int
    intervention_date: date
    intervention_type: str
    raw_status: str
    net_gain_bbl: float | None
    cost_usd: float
    contractor: str

    def raw_row(self) -> tuple[object, ...]:
        return (
            self.intervention_id, self.well_id, self.intervention_date,
            self.intervention_type, self.raw_status, self.net_gain_bbl,
            self.cost_usd, self.contractor,
        )

    def curated_row(self) -> tuple[object, ...]:
        row = self.raw_row()
        return (*row[:4], curated_status(self.raw_status), *row[5:])


def generate_interventions(
    config: SeedConfig,
    wells: Sequence[Well],
    rng: random.Random | None = None,
) -> list[Intervention]:
    rng = rng or random.Random(config.seed + 100)
    orphan_ids = (9001, 9002, 9003)
    wells_by_id = {well.well_id: well for well in wells}
    interventions: list[Intervention] = []
    for job_id in range(1, config.intervention_count + 1):
        well_id = rng.randint(1, config.well_count)
        if ((config.name == "realistic" and job_id % 500 == 0)
                or (config.name == "demo" and rng.random() < 0.002)):
            well_id = rng.choice(orphan_ids)

        intervention_type = rng.choice(INTERVENTION_TYPES)
        if config.name == "realistic" and job_id % 100 == 0:
            raw_status = str(((job_id // 100) - 1) % 3 + 1)
        elif config.name == "demo" and rng.random() < 0.01:
            raw_status = rng.choice(("1", "2", "3"))
        else:
            raw_status = rng.choices(STATUSES, weights=[75, 12, 13])[0]

        if rng.random() < 0.03:
            net_gain = None
        else:
            base = {
                "FRAC": 3200,
                "WORKOVER": 1400,
                "ACIDIZING": 900,
                "PERFORATION": 1800,
            }[intervention_type]
            net_gain = round(rng.gauss(base, base * 0.35), 2)
        intervention_start = config.intervention_start
        if config.name == "realistic" and well_id in wells_by_id:
            intervention_start = max(
                intervention_start, wells_by_id[well_id].spud_date,
            )
        interventions.append(Intervention(
            intervention_id=job_id,
            well_id=well_id,
            intervention_date=random_date(
                rng, intervention_start, config.intervention_end
            ),
            intervention_type=intervention_type,
            raw_status=raw_status,
            net_gain_bbl=net_gain,
            cost_usd=round(rng.gauss(250_000, 90_000), 2),
            contractor=rng.choice(CONTRACTORS),
        ))
    return interventions


def generate_downtime_events(
    config: SeedConfig, wells: Sequence[Well]
) -> list[DowntimeEvent]:
    rng = random.Random(config.seed + 2_000)
    producers = [well for well in wells if well.well_type == "PRODUCER"]
    categories = ("MECHANICAL", "ELECTRICAL", "FACILITY", "WEATHER", "PLANNED_MAINTENANCE")
    events: list[DowntimeEvent] = []
    for event_id in range(1, config.downtime_event_count + 1):
        well = producers[rng.randrange(len(producers))]
        event_day = random_date(rng, config.production_start, config.production_end)
        event_start = datetime.combine(event_day, datetime.min.time()) + timedelta(
            hours=rng.randrange(24), minutes=rng.randrange(60)
        )
        duration = round(rng.uniform(1.0, 72.0), 2)
        category = rng.choice(categories)
        events.append(DowntimeEvent(
            event_id=event_id,
            well_id=well.well_id,
            event_start=event_start,
            event_end=event_start + timedelta(hours=duration),
            duration_hours=duration,
            downtime_category=category,
            planning_status=("PLANNED" if category == "PLANNED_MAINTENANCE" else "UNPLANNED"),
        ))
    return events


@dataclass(frozen=True)
class FieldTarget:
    field_name: str
    target_month: date
    asset_name: str
    region_name: str
    operator_name: str
    basin_name: str
    target_oil_bbl: float

    def curated_row(self) -> tuple[object, ...]:
        return (
            self.field_name, self.target_month, self.asset_name,
            self.region_name, self.operator_name, self.basin_name,
            self.target_oil_bbl,
        )


@dataclass(frozen=True)
class ProductionPerformance:
    field_name: str
    performance_month: date
    asset_name: str
    region_name: str
    operator_name: str
    basin_name: str
    actual_oil_bbl: float
    target_oil_bbl: float
    variance_oil_bbl: float
    attainment_pct: float

    def curated_row(self) -> tuple[object, ...]:
        return (
            self.field_name, self.performance_month, self.asset_name,
            self.region_name, self.operator_name, self.basin_name,
            self.actual_oil_bbl, self.target_oil_bbl,
            self.variance_oil_bbl, self.attainment_pct,
        )


def iter_month_starts(config: SeedConfig) -> Iterator[date]:
    year = config.production_start.year
    month = config.production_start.month
    for _ in range(config.month_count):
        yield date(year, month, 1)
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def generate_monthly_performance(
    config: SeedConfig,
    wells: Sequence[Well],
    monthly_actuals: dict[tuple[str, date], float],
) -> tuple[list[FieldTarget], list[ProductionPerformance]]:
    metadata: dict[str, tuple[str, str, str, str]] = {}
    for well in wells:
        metadata.setdefault(
            well.field_name,
            (well.asset_name, well.region_name, well.operator_name, well.basin_name),
        )
    targets: list[FieldTarget] = []
    performance: list[ProductionPerformance] = []
    for field_index, field_name in enumerate(sorted(metadata)):
        asset, region, operator, basin = metadata[field_name]
        for month_index, month in enumerate(iter_month_starts(config)):
            actual = round(monthly_actuals.get((field_name, month), 0.0), 2)
            factor = 0.92 + ((field_index * 7 + month_index * 3) % 17) / 100
            target = round(max(1.0, actual * factor), 2)
            variance = round(actual - target, 2)
            attainment = round(actual / target * 100, 4)
            targets.append(FieldTarget(
                field_name, month, asset, region, operator, basin, target,
            ))
            performance.append(ProductionPerformance(
                field_name, month, asset, region, operator, basin,
                actual, target, variance, attainment,
            ))
    return targets, performance


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days))


@dataclass(frozen=True)
class SeedSummary:
    profile: str
    wells: int
    interventions: int
    production: int
    downtime_events: int
    targets: int
    performance: int
    elapsed_seconds: float


def _warehouse_has_rows(cur: psycopg.Cursor) -> bool:
    cur.execute(
        """
        SELECT EXISTS (SELECT 1 FROM ddh.dim_wells)
            OR EXISTS (SELECT 1 FROM ddh.fct_well_interventions)
            OR EXISTS (SELECT 1 FROM ddh.fct_production_daily)
            OR EXISTS (SELECT 1 FROM ddh.fct_downtime_events)
            OR EXISTS (SELECT 1 FROM ddh.fct_field_targets_monthly)
            OR EXISTS (SELECT 1 FROM ddh.mart_production_performance_monthly)
            OR EXISTS (SELECT 1 FROM stg.wells_raw)
            OR EXISTS (SELECT 1 FROM stg.interventions_raw)
            OR EXISTS (SELECT 1 FROM stg.production_daily_raw)
            OR EXISTS (SELECT 1 FROM stg.downtime_events_raw)
            OR EXISTS (SELECT 1 FROM stg.field_targets_monthly_raw)
        """
    )
    return bool(cur.fetchone()[0])


def seed_database(dsn: str, config: SeedConfig) -> SeedSummary:
    """Write one deterministic profile atomically.

    The historical demo command keeps its replace-in-place behavior.  The
    realistic profile is intentionally stricter: its dedicated database must
    be empty, so the opt-in command can never erase an ordinary warehouse.
    """
    started = time.perf_counter()
    rng = random.Random(config.seed)
    wells = generate_wells(config, rng)
    interventions = generate_interventions(config, wells, rng)
    downtime_events = generate_downtime_events(config, wells)
    wells_by_id = {well.well_id: well for well in wells}
    monthly_actuals: dict[tuple[str, date], float] = {}

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if config.name == "realistic" and _warehouse_has_rows(cur):
            raise RuntimeError(
                "the realistic target database already contains warehouse data; "
                "refusing to truncate it"
            )
        if config.name == "demo":
            cur.execute(
                """
                TRUNCATE
                    ddh.dim_wells,
                    ddh.fct_well_interventions,
                    ddh.fct_production_daily,
                    ddh.fct_downtime_events,
                    ddh.fct_field_targets_monthly,
                    ddh.mart_production_performance_monthly,
                    stg.wells_raw,
                    stg.interventions_raw,
                    stg.production_daily_raw,
                    stg.downtime_events_raw,
                    stg.field_targets_monthly_raw
                """
            )

        with cur.copy(
            "COPY ddh.dim_wells (well_id, well_name, region_name, field_name, "
            "spud_date, well_type, latitude, longitude, asset_name, "
            "operator_name, basin_name, operating_status, lift_method) FROM STDIN"
        ) as copy:
            for well in wells:
                copy.write_row(well.curated_row())

        with cur.copy(
            "COPY ddh.fct_well_interventions (intervention_id, well_id, "
            "intervention_date, intervention_type, status, net_gain_bbl, "
            "cost_usd, contractor) FROM STDIN"
        ) as copy:
            for intervention in interventions:
                copy.write_row(intervention.curated_row())

        production_count = 0
        with cur.copy(
            "COPY ddh.fct_production_daily (well_id, reading_date, oil_bbl, "
            "gas_mcf, water_bbl, downtime_hours) FROM STDIN"
        ) as copy:
            # The iterator emits one row at a time; the realistic path never
            # constructs a 1.4M-element Python list.
            for row in iter_production_rows(config, wells, rng):
                copy.write_row(row)
                well_id, reading, oil = row[0], row[1], row[2]
                month = date(reading.year, reading.month, 1)
                key = (wells_by_id[well_id].field_name, month)
                monthly_actuals[key] = monthly_actuals.get(key, 0.0) + oil
                production_count += 1

        # The landing mirror is populated set-wise from the streamed fact, so
        # mirroring does not force a second 1.4M-row Python pass or object list.
        cur.execute(
            """
            INSERT INTO stg.production_daily_raw
                (well_id, reading_dt, oil_vol, gas_vol, water_vol, down_hrs)
            SELECT well_id::text, reading_date::text, oil_bbl::text,
                   gas_mcf::text, water_bbl::text, downtime_hours::text
              FROM ddh.fct_production_daily
            """
        )

        with cur.copy(
            "COPY ddh.fct_downtime_events (event_id, well_id, event_start, "
            "event_end, duration_hours, downtime_category, planning_status) "
            "FROM STDIN"
        ) as copy:
            for event in downtime_events:
                copy.write_row(event.curated_row())

        targets, performance = generate_monthly_performance(
            config, wells, monthly_actuals
        )
        with cur.copy(
            "COPY ddh.fct_field_targets_monthly (field_name, target_month, "
            "asset_name, region_name, operator_name, basin_name, target_oil_bbl) "
            "FROM STDIN"
        ) as copy:
            for target in targets:
                copy.write_row(target.curated_row())
        with cur.copy(
            "COPY ddh.mart_production_performance_monthly (field_name, "
            "performance_month, asset_name, region_name, operator_name, "
            "basin_name, actual_oil_bbl, target_oil_bbl, variance_oil_bbl, "
            "attainment_pct) FROM STDIN"
        ) as copy:
            for row in performance:
                copy.write_row(row.curated_row())

        with cur.copy(
            "COPY stg.wells_raw (well_id, wellname, region, fieldname, spud_dt, "
            "welltype, asset, operator_name, basin, op_status, lift) FROM STDIN"
        ) as copy:
            for well in wells:
                raw = (
                    well.well_id, well.well_name, well.region_name,
                    well.field_name, well.spud_date, well.well_type,
                    well.asset_name, well.operator_name, well.basin_name,
                    well.operating_status, well.lift_method,
                )
                copy.write_row(tuple(str(value) for value in raw))

        with cur.copy(
            "COPY stg.interventions_raw (job_id, well_id, job_dt, job_type, "
            "stat, gain, cost, contractor) FROM STDIN"
        ) as copy:
            for intervention in interventions:
                copy.write_row(tuple(
                    None if value is None else str(value)
                    for value in intervention.raw_row()
                ))

        with cur.copy(
            "COPY stg.downtime_events_raw (event_id, well_id, start_ts, end_ts, "
            "duration_hrs, reason, plan_stat) FROM STDIN"
        ) as copy:
            for event in downtime_events:
                copy.write_row(tuple(str(value) for value in event.curated_row()))

        with cur.copy(
            "COPY stg.field_targets_monthly_raw (fieldname, target_dt, asset, "
            "region, operator, basin, oil_target) FROM STDIN"
        ) as copy:
            for target in targets:
                copy.write_row(tuple(str(value) for value in target.curated_row()))

        cur.execute("ANALYZE")

    return SeedSummary(
        profile=config.name,
        wells=len(wells),
        interventions=len(interventions),
        production=production_count,
        downtime_events=len(downtime_events),
        targets=len(targets),
        performance=len(performance),
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )


def main(argv: Sequence[str] | None = None) -> int:
    command = parse_seed_command(argv)
    config = command.config
    if config.name == "realistic":
        try:
            # Confirmation is evaluated before ADMIN_URL is read, and the
            # live database identity is checked before generation or writes.
            if command.confirmation != REALISTIC_DB:
                raise ValueError(
                    "realistic seeding is opt-in; rerun with "
                    "CONFIRM=semantic_realistic"
                )
            dsn = os.environ["ADMIN_URL"]
            require_realistic_cli_access(dsn, command.confirmation)
            summary = seed_database(dsn, config)
        except (KeyError, ValueError, RuntimeError, psycopg.Error) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        # Preserve the historical no-argument demo entry point exactly.
        summary = seed_database(os.environ["ADMIN_URL"], config)
    print(
        f"seeded {summary.profile}: {summary.wells} wells, "
        f"{summary.interventions} interventions, "
        f"{summary.production} production rows, "
        f"{summary.downtime_events} downtime events, "
        f"{summary.targets} targets, {summary.performance} performance rows "
        f"in {summary.elapsed_seconds:.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

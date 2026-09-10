"""Fast contracts for the opt-in synthetic warehouse profiles.

These tests deliberately inspect configuration and small iterator prefixes.  The
ordinary suite must never allocate the realistic profile's 1.4M production rows.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import importlib.util
from itertools import islice
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest


def _load_module(name: str, relative_path: str):
    container_path = Path("/") / relative_path
    path = container_path if container_path.exists() else Path(__file__).parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seed = _load_module("warehouse_seed", "db/seed/seed.py")


def test_no_argument_profile_preserves_the_fast_demo_contract():
    config = seed.parse_seed_config([])

    assert config.name == "demo"
    assert config.production_start == date(2024, 8, 1)
    assert config.production_end == date(2026, 8, 1)
    assert config.well_count == 200
    assert config.intervention_count == 4_000
    assert (config.producer_count, config.injector_count, config.observation_count) == (
        162, 28, 10,
    )
    assert config.production_row_count == 118_260


def test_demo_core_rows_keep_the_historical_rng_sequence():
    """These literals are the pre-profile rows already shipped in semantic_test."""
    config = seed.parse_seed_config([])
    rng = random.Random(42)

    wells = seed.generate_wells(config, rng)
    interventions = seed.generate_interventions(config, wells, rng)
    first_production = next(seed.iter_production_rows(config, wells, rng))

    assert wells[0].curated_row()[:8] == (
        1, "NF-0001", "Mangystau", "Uzen", date(2010, 5, 4),
        "PRODUCER", 43.863611, 53.096607,
    )
    assert interventions[0].curated_row() == (
        1, 141, date(2025, 12, 19), "PERFORATION", "COMPLETED",
        1572.95, 224701.21, "northfieldservice",
    )
    assert first_production == (
        1, date(2024, 8, 1), 416.61, 1751.02, 604.13, 0.0,
    )


def test_realistic_defaults_have_the_exact_requested_horizon_and_counts():
    config = seed.parse_seed_config(["--profile", "realistic"])

    assert config.production_start == date(2018, 8, 1)
    assert config.production_end == date(2026, 8, 1)
    assert config.production_days == 2_922
    assert config.well_count == 600
    assert config.producer_count == 480
    assert config.injector_count == 90
    assert config.observation_count == 30
    assert config.field_count == 24
    assert config.intervention_count == 16_000
    assert config.production_row_count == 1_402_560
    assert config.target_row_count == 2_304


@pytest.mark.parametrize(
    ("end", "expected_start", "days"),
    [
        ("2025-01-15", date(2017, 1, 15), 2_922),
        ("2028-02-29", date(2020, 2, 29), 2_922),
    ],
)
def test_realistic_explicit_end_date_preserves_an_eight_year_horizon(
    end: str, expected_start: date, days: int
):
    config = seed.parse_seed_config(
        ["--profile", "realistic", "--end-date", end]
    )

    assert config.production_start == expected_start
    assert config.production_end == date.fromisoformat(end)
    assert config.production_days == days
    assert config.target_row_count == 24 * 97


def test_realistic_wells_are_exactly_distributed_and_repeatable():
    config = seed.parse_seed_config(["--profile", "realistic"])

    first = seed.generate_wells(config)
    second = seed.generate_wells(config)

    assert first == second
    assert len(first) == 600
    assert Counter(w.well_type for w in first) == {
        "PRODUCER": 480,
        "INJECTOR": 90,
        "OBSERVATION": 30,
    }
    assert len(Counter(w.field_name for w in first)) == 24
    assert set(Counter(w.field_name for w in first).values()) == {25}
    assert all(
        (w.asset_name, w.operator_name, w.basin_name, w.operating_status, w.lift_method)
        for w in first
    )


def test_realistic_production_is_a_lazy_deterministic_stream():
    config = seed.parse_seed_config(["--profile", "realistic"])
    wells = seed.generate_wells(config)

    rows = seed.iter_production_rows(config, wells)

    assert iter(rows) is rows
    assert list(islice(rows, 2)) == list(
        islice(seed.iter_production_rows(config, wells), 2)
    )


def test_legacy_status_codes_have_a_documented_curated_mapping():
    assert seed.curated_status("1") == "COMPLETED"
    assert seed.curated_status("2") == "CANCELLED"
    assert seed.curated_status("3") == "IN_PROGRESS"
    assert seed.curated_status("completed") == "COMPLETED"


def test_realistic_interventions_keep_dirty_raw_values_but_curate_codes():
    config = seed.parse_seed_config(["--profile", "realistic"])
    wells = seed.generate_wells(config)

    first = seed.generate_interventions(config, wells)
    second = seed.generate_interventions(config, wells)

    assert first == second
    assert len(first) == 16_000
    coded = [job for job in first if job.raw_status in {"1", "2", "3"}]
    assert coded
    assert {job.raw_status for job in coded} == {"1", "2", "3"}
    assert {job.curated_row()[4] for job in coded} <= {
        "COMPLETED", "CANCELLED", "IN_PROGRESS"
    }
    assert any(job.net_gain_bbl is None for job in first)
    assert any(job.well_id > config.well_count for job in first)


def test_realistic_downtime_is_deterministic_without_reading_production():
    config = seed.parse_seed_config(["--profile", "realistic"])
    wells = seed.generate_wells(config)

    first = seed.generate_downtime_events(config, wells)
    second = seed.generate_downtime_events(config, wells)

    assert first == second
    assert len(first) == config.downtime_event_count
    assert all(config.production_start <= event.event_start.date() < config.production_end
               for event in first)


def test_realistic_facts_never_precede_their_non_orphan_well_spud():
    config = seed.parse_seed_config(["--profile", "realistic"])
    wells = seed.generate_wells(config)
    by_id = {well.well_id: well for well in wells}

    assert all(
        well.spud_date <= config.production_start
        for well in wells
        if well.well_type == "PRODUCER"
    )
    assert all(
        job.well_id not in by_id
        or job.intervention_date >= by_id[job.well_id].spud_date
        for job in seed.generate_interventions(config, wells)
    )
    assert all(
        event.event_start.date() >= by_id[event.well_id].spud_date
        for event in seed.generate_downtime_events(config, wells)
    )


def test_monthly_targets_and_performance_cover_each_field_month():
    config = seed.parse_seed_config(["--profile", "realistic"])
    wells = seed.generate_wells(config)
    actuals = {
        (field, month): 1_000.0
        for field in {well.field_name for well in wells}
        for month in seed.iter_month_starts(config)
    }

    targets, performance = seed.generate_monthly_performance(
        config, wells, actuals
    )

    assert len(targets) == config.target_row_count
    assert len(performance) == config.target_row_count
    assert {(row.field_name, row.target_month) for row in targets} == set(actuals)
    sample = performance[0]
    assert sample.variance_oil_bbl == round(
        sample.actual_oil_bbl - sample.target_oil_bbl, 2
    )
    assert sample.attainment_pct == round(
        sample.actual_oil_bbl / sample.target_oil_bbl * 100, 4
    )


def test_realistic_confirmation_is_checked_before_any_database_work():
    provision = _load_module(
        "realistic_provision", "scripts/ensure_realistic_db.py"
    )

    with pytest.raises(ValueError, match="CONFIRM=semantic_realistic"):
        provision.require_confirmation("")
    with pytest.raises(ValueError, match="CONFIRM=semantic_realistic"):
        provision.require_confirmation("semantic")
    provision.require_confirmation("semantic_realistic")


def test_realistic_provision_cli_fails_closed_without_confirmation():
    path = Path("/scripts/ensure_realistic_db.py")
    if not path.exists():
        path = Path(__file__).parents[2] / "scripts/ensure_realistic_db.py"

    result = subprocess.run(
        [sys.executable, str(path)],
        text=True,
        capture_output=True,
        env={},
        check=False,
    )

    assert result.returncode == 2
    assert "CONFIRM=semantic_realistic" in result.stderr


def test_direct_realistic_seed_cli_fails_closed_without_confirmation():
    path = Path("/db/seed/seed.py")
    if not path.exists():
        path = Path(__file__).parents[2] / "db/seed/seed.py"

    result = subprocess.run(
        [sys.executable, str(path), "--profile", "realistic"],
        text=True,
        capture_output=True,
        env={},
        check=False,
    )

    assert result.returncode == 2
    assert "CONFIRM=semantic_realistic" in result.stderr


def test_direct_realistic_seed_cli_rejects_an_ordinary_database():
    path = Path("/db/seed/seed.py")
    if not path.exists():
        path = Path(__file__).parents[2] / "db/seed/seed.py"

    result = subprocess.run(
        [
            sys.executable,
            str(path),
            "--profile",
            "realistic",
            "--confirm",
            "semantic_realistic",
        ],
        text=True,
        capture_output=True,
        env={"ADMIN_URL": os.environ["ADMIN_URL"]},
        check=False,
    )

    assert result.returncode == 2
    assert "must target database semantic_realistic" in result.stderr

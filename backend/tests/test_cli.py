"""Offline completion-gate commands."""

from __future__ import annotations

import sys
from pathlib import Path

from app import cli
from app import migrations


_VALID_ENTITY = """\
entity: production
label: Production
table: ddh.production
dimensions:
  region:
    label: Region
    type: string
measures:
  oil:
    label: Oil
    agg: sum
    column: oil_bbl
"""


def _configured_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    layer_dir = tmp_path / "layer"
    migration_dir = tmp_path / "migrations"
    layer_dir.mkdir()
    migration_dir.mkdir()
    monkeypatch.setattr(cli.settings, "layer_dir", layer_dir)
    monkeypatch.setattr(cli.settings, "migration_dir", migration_dir)
    return layer_dir, migration_dir


def test_validate_all_cli_validates_configured_files_without_connecting(
    tmp_path: Path, monkeypatch, capsys
):
    """Removing the loader or using the migration runner would break this."""
    layer_dir, migration_dir = _configured_paths(tmp_path, monkeypatch)
    (layer_dir / "production.yaml").write_text(_VALID_ENTITY)
    (migration_dir / "0001_initial.sql").write_text("SELECT 1;\n")
    monkeypatch.setattr(
        migrations.psycopg,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("offline only")),
    )
    monkeypatch.setattr(sys, "argv", ["app.cli", "validate-all"])

    assert cli.main() == 0
    assert capsys.readouterr().out == (
        "validated 1 semantic definition(s) and 1 migration file(s)\n"
    )


def test_validate_all_cli_redacts_structural_validation_errors(
    tmp_path: Path, monkeypatch, capsys
):
    """Printing a definition's contents on failure would leak this sentinel."""
    layer_dir, _ = _configured_paths(tmp_path, monkeypatch)
    secret = "warehouse-secret-never-print"
    (layer_dir / "production.yaml").write_text(f"entity: {secret}\n")
    monkeypatch.setattr(sys, "argv", ["app.cli", "validate-all"])

    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "validation failed\n"
    assert secret not in captured.err


def test_validate_all_cli_redacts_malformed_yaml(
    tmp_path: Path, monkeypatch, capsys
):
    """A YAML parser error must not turn the gate into a traceback."""
    layer_dir, _ = _configured_paths(tmp_path, monkeypatch)
    (layer_dir / "production.yaml").write_text("entity: [unclosed\n")
    monkeypatch.setattr(sys, "argv", ["app.cli", "validate-all"])

    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "validation failed\n"


def test_validate_all_cli_redacts_invalid_utf8_definition(
    tmp_path: Path, monkeypatch, capsys
):
    """An invalid definition encoding must not escape as a traceback."""
    layer_dir, _ = _configured_paths(tmp_path, monkeypatch)
    (layer_dir / "production.yaml").write_bytes(b"entity: production\n\xff")
    monkeypatch.setattr(sys, "argv", ["app.cli", "validate-all"])

    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "validation failed\n"

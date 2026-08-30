"""Tests for the macro registry database (M6). See db.py for module purpose."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a fresh file per test, so tests never touch the
    real registry.db or leak state between each other.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_registry.db")
    db.init_db()
    yield


def test_db_path_defaults_to_the_module_directory_when_env_var_unset(monkeypatch):
    """No REGISTRY_DB_PATH set in this test process's environment (the
    normal case for local dev/CI) -- db.py's module-level code evaluated
    at import time with no env var set should have set DB_PATH to the
    module-relative default.
    """
    # db.py's module-level init code only runs once per process, and this
    # test process already imported db (with the fixture's monkeypatch
    # applied on top). So spawn a fresh Python process with
    # REGISTRY_DB_PATH excluded from its environment, import db fresh
    # there, and check what db.DB_PATH resolved to -- this exercises
    # db.py's own module-level initialization logic directly, rather than
    # a re-derived copy of it in the test.
    result = subprocess.run(
        [sys.executable, "-c", "import db; print(db.DB_PATH)"],
        env={k: v for k, v in os.environ.items() if k != "REGISTRY_DB_PATH"},
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    )
    # Should be the module-relative path (next to db.py)
    assert result.stdout.strip() == str(Path(__file__).parent / "registry.db")


def test_db_path_reads_from_registry_db_path_env_var_when_set(tmp_path):
    """The Deployment (infra/backend-api.yaml) sets REGISTRY_DB_PATH to a
    PVC-mounted path -- confirm db.py's own DB_PATH actually reads it at
    import time, in a fresh process (module-level code only runs once per
    process, so this can't be tested by mutating os.environ after db.py
    has already been imported in this test process).
    """
    override_path = tmp_path / "data" / "registry.db"
    result = subprocess.run(
        [sys.executable, "-c", "import db; print(db.DB_PATH)"],
        env={**os.environ, "REGISTRY_DB_PATH": str(override_path)},
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == str(override_path)


def _upsert(technical_name="rtwp-anomaly-demo", **overrides):
    fields = {
        "display_name": "RTWP Anomaly Detector",
        "description": "Flags cells with high uplink noise.",
        "icon": "signal",
        "source_code": "import pandas as pd\n",
        "image_tag": f"{technical_name}:generated",
        "built_at": "2026-08-10T10:00:00+00:00",
        "updated_at": "2026-08-10T10:00:00+00:00",
    }
    fields.update(overrides)
    db.upsert_macro(technical_name, **fields)


def test_list_macros_is_empty_on_a_fresh_database():
    assert db.list_macros() == []


def test_upsert_then_list_returns_the_inserted_macro():
    _upsert()

    rows = db.list_macros()

    assert len(rows) == 1
    assert rows[0]["technical_name"] == "rtwp-anomaly-demo"
    assert rows[0]["display_name"] == "RTWP Anomaly Detector"
    assert rows[0]["icon"] == "signal"


def test_upsert_with_same_technical_name_overwrites_not_duplicates():
    _upsert(display_name="First version", image_tag="rtwp-anomaly-demo:generated")
    _upsert(display_name="Second version", image_tag="rtwp-anomaly-demo:generated")

    rows = db.list_macros()

    assert len(rows) == 1
    assert rows[0]["display_name"] == "Second version"


def test_upsert_rejects_an_invalid_icon():
    with pytest.raises(db.InvalidIconError):
        _upsert(icon="not-a-real-icon")


def test_upsert_accepts_every_valid_icon():
    for i, icon in enumerate(sorted(db.VALID_ICONS)):
        _upsert(technical_name=f"macro-{i}", icon=icon)

    rows = db.list_macros()
    assert len(rows) == len(db.VALID_ICONS)


def test_get_macro_returns_none_for_an_unknown_name():
    assert db.get_macro("never-built") is None


def test_get_macro_returns_the_full_record_including_source_code():
    _upsert(source_code="import os\nprint('hi')\n")

    row = db.get_macro("rtwp-anomaly-demo")

    assert row is not None
    assert row["source_code"] == "import os\nprint('hi')\n"


def test_list_macros_orders_most_recently_built_first():
    _upsert(technical_name="older", built_at="2026-08-01T00:00:00+00:00")
    _upsert(technical_name="newer", built_at="2026-08-09T00:00:00+00:00")

    rows = db.list_macros()

    assert [row["technical_name"] for row in rows] == ["newer", "older"]


def test_delete_macro_returns_false_for_an_unknown_name():
    assert db.delete_macro("never-built") is False


def test_delete_macro_returns_true_and_removes_the_row():
    _upsert()

    deleted = db.delete_macro("rtwp-anomaly-demo")

    assert deleted is True
    assert db.get_macro("rtwp-anomaly-demo") is None


def test_delete_macro_does_not_affect_other_rows():
    _upsert(technical_name="keep-me")
    _upsert(technical_name="delete-me")

    db.delete_macro("delete-me")

    assert db.get_macro("keep-me") is not None
    assert db.get_macro("delete-me") is None


def test_new_macro_has_a_null_gitea_repo_url_by_default():
    _upsert()

    row = db.get_macro("rtwp-anomaly-demo")

    assert row["gitea_repo_url"] is None


def test_update_gitea_url_sets_the_column():
    _upsert()

    db.update_gitea_url("rtwp-anomaly-demo", "http://gitea:3000/admin/rtwp-anomaly-demo")

    row = db.get_macro("rtwp-anomaly-demo")
    assert row["gitea_repo_url"] == "http://gitea:3000/admin/rtwp-anomaly-demo"


def test_update_gitea_url_does_not_affect_other_rows():
    _upsert(technical_name="macro-a")
    _upsert(technical_name="macro-b")

    db.update_gitea_url("macro-a", "http://gitea:3000/admin/macro-a")

    assert db.get_macro("macro-b")["gitea_repo_url"] is None


def _insert_execution(job_name="rtwp-anomaly-demo-abc123", **overrides):
    fields = {
        "macro_name": "rtwp-anomaly-demo",
        "status": "pending",
        "created_at": "2026-08-17T10:00:00+00:00",
    }
    fields.update(overrides)
    db.insert_execution(job_name, **fields)


def test_list_executions_is_empty_on_a_fresh_database():
    assert db.list_executions() == []


def test_insert_execution_then_list_returns_it_with_null_finished_at():
    _insert_execution()

    rows = db.list_executions()

    assert len(rows) == 1
    assert rows[0]["job_name"] == "rtwp-anomaly-demo-abc123"
    assert rows[0]["macro_name"] == "rtwp-anomaly-demo"
    assert rows[0]["status"] == "pending"
    assert rows[0]["created_at"] == "2026-08-17T10:00:00+00:00"
    assert rows[0]["finished_at"] is None


def test_list_executions_orders_most_recently_created_first():
    _insert_execution(job_name="older", created_at="2026-08-01T00:00:00+00:00")
    _insert_execution(job_name="newer", created_at="2026-08-09T00:00:00+00:00")

    rows = db.list_executions()

    assert [row["job_name"] for row in rows] == ["newer", "older"]


def test_get_execution_returns_none_for_an_unknown_job_name():
    assert db.get_execution("never-ran") is None


def test_get_execution_returns_the_row():
    _insert_execution()

    row = db.get_execution("rtwp-anomaly-demo-abc123")

    assert row is not None
    assert row["macro_name"] == "rtwp-anomaly-demo"


def test_update_execution_status_sets_status_and_finished_at():
    _insert_execution()

    db.update_execution_status(
        "rtwp-anomaly-demo-abc123", status="succeeded", finished_at="2026-08-17T10:05:00+00:00"
    )

    row = db.get_execution("rtwp-anomaly-demo-abc123")
    assert row["status"] == "succeeded"
    assert row["finished_at"] == "2026-08-17T10:05:00+00:00"


def test_update_execution_status_does_not_affect_other_rows():
    _insert_execution(job_name="macro-a-1")
    _insert_execution(job_name="macro-b-1")

    db.update_execution_status("macro-a-1", status="succeeded", finished_at="2026-08-17T10:05:00+00:00")

    row_b = db.get_execution("macro-b-1")
    assert row_b["status"] == "pending"
    assert row_b["finished_at"] is None


def test_init_db_adds_gitea_repo_url_to_a_pre_existing_table(tmp_path, monkeypatch):
    """A database file created before this column existed -- simulated by
    creating the table by hand without it -- gets the column added on the
    next init_db() call, not left behind or errored on.
    """
    db_path = tmp_path / "pre_existing.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    with db._connect() as conn:
        conn.execute(
            """
            CREATE TABLE macros (
                technical_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                description TEXT,
                icon TEXT NOT NULL,
                source_code TEXT NOT NULL,
                image_tag TEXT NOT NULL,
                built_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    db.init_db()
    _upsert()

    row = db.get_macro("rtwp-anomaly-demo")
    assert row["gitea_repo_url"] is None

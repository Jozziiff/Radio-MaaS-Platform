"""Tests for the macro registry database (M6). See db.py for module purpose."""

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

"""Tests for the rtwp-anomaly-demo macro (M2). See macro.py for module purpose."""

import pandas as pd

from macro import flag_anomalies


def test_flags_rows_above_threshold_as_anomaly():
    df = pd.DataFrame({
        "cell_id": ["A1", "B2"],
        "rtwp_dbm": [-80.0, -95.0],
    })

    result = flag_anomalies(df)

    assert list(result["status"]) == ["anomaly", "ok"]


def test_row_exactly_at_threshold_is_not_flagged():
    df = pd.DataFrame({
        "cell_id": ["A1"],
        "rtwp_dbm": [-85.0],
    })

    result = flag_anomalies(df)

    assert result["status"].iloc[0] == "ok"


def test_preserves_input_columns_and_order():
    df = pd.DataFrame({
        "cell_id": ["A1"],
        "rtwp_dbm": [-70.0],
    })

    result = flag_anomalies(df)

    assert list(result.columns) == ["cell_id", "rtwp_dbm", "status"]

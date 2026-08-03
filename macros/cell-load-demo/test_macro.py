"""Tests for the cell-load-demo macro (M1). See macro.py for module purpose."""

import pandas as pd

from macro import flag_high_load


def test_flags_rows_above_threshold_as_overload():
    df = pd.DataFrame({
        "cell_id": ["A1", "B2"],
        "load_percent": [85.0, 50.0],
    })

    result = flag_high_load(df)

    assert list(result["status"]) == ["overload", "ok"]


def test_row_exactly_at_threshold_is_not_flagged():
    df = pd.DataFrame({
        "cell_id": ["A1"],
        "load_percent": [80.0],
    })

    result = flag_high_load(df)

    assert result["status"].iloc[0] == "ok"


def test_preserves_input_columns_and_order():
    df = pd.DataFrame({
        "cell_id": ["A1"],
        "load_percent": [90.0],
    })

    result = flag_high_load(df)

    assert list(result.columns) == ["cell_id", "load_percent", "status"]

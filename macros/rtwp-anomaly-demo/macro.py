"""rtwp-anomaly-demo macro (M2): flags cells with high RTWP (uplink noise).

Independent of cell-load-demo by design — it exists to test whether the AST
engine's column detection generalizes to a different access pattern
(row["..."] inside df.iterrows(), not df["..."]) and a different way of
building the output (a fresh per-row dict, not a subscript assignment on a
copied DataFrame). Deliberately simple: this exercises the pipeline, not a
realistic RTWP analysis.
"""

import os

import pandas as pd

RTWP_THRESHOLD_DBM = -85.0


def flag_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Mark each row "anomaly" if rtwp_dbm exceeds the threshold, else "ok".

    Reads each row's cell_id/rtwp_dbm via `row["..."]` inside a
    `df.iterrows()` loop and builds the result as a list of per-row dict
    literals, rather than copying `df` and assigning a new column onto it —
    so every output column is named explicitly in the source, with nothing
    carried through implicitly.

    Args:
        df: DataFrame with `cell_id` and `rtwp_dbm` columns.

    Returns:
        A new DataFrame with columns cell_id, rtwp_dbm, status.
    """
    records = []
    for _, row in df.iterrows():
        cell_id = row["cell_id"]
        rtwp_dbm = row["rtwp_dbm"]
        status = "anomaly" if rtwp_dbm > RTWP_THRESHOLD_DBM else "ok"
        records.append({"cell_id": cell_id, "rtwp_dbm": rtwp_dbm, "status": status})
    return pd.DataFrame(records)


def main() -> None:
    """Read INPUT_PATH, flag RTWP anomalies, write the result to OUTPUT_PATH."""
    input_path = os.environ["INPUT_PATH"]
    output_path = os.environ["OUTPUT_PATH"]

    df = pd.read_csv(input_path)
    result = flag_anomalies(df)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()

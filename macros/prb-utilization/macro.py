"""prb-utilization macro (M6): flags cells with congested PRB usage.

Named "PRB Utilization" as a deliberate nod to the original PFE report
this project's architecture is guided by -- prb-utilization was the exact
macro name used in that report's own CI/CD demo.

Uses plain df["col"] subscript reads, same family as
handover-success-rate but built via dict.fromkeys()-style column
assignment on an empty DataFrame rather than a copy-then-assign -- a
distinct construction shape while staying within what ast_engine.py's
_collect_required_columns can actually detect: it only matches
`ast.Subscript` nodes where the object being subscripted is a bare
`ast.Name` with a string constant key, so `df["col"]` counts but
`df.loc[:, "col"]` (an Attribute access, not a bare Name) doesn't --
confirmed by trying the .loc style first and finding it detected zero
required columns.
"""

import os

import pandas as pd

UTILIZATION_CONGESTED_THRESHOLD = 80.0


def compute_utilization(df: pd.DataFrame) -> pd.DataFrame:
    """Compute PRB utilization per cell and flag congested ones.

    Args:
        df: DataFrame with cell_id, prb_used, prb_total.

    Returns:
        A new DataFrame with cell_id, prb_used, prb_total,
        utilization_percent, status.
    """
    cell_id = df["cell_id"]
    prb_used = df["prb_used"]
    prb_total = df["prb_total"]
    utilization_percent = prb_used / prb_total * 100
    status = utilization_percent.apply(
        lambda pct: "congested" if pct > UTILIZATION_CONGESTED_THRESHOLD else "ok"
    )
    return pd.DataFrame(
        {
            "cell_id": cell_id,
            "prb_used": prb_used,
            "prb_total": prb_total,
            "utilization_percent": utilization_percent,
            "status": status,
        }
    )


def main() -> None:
    """Read INPUT_PATH, compute PRB utilization, write the result to OUTPUT_PATH."""
    input_path = os.environ["INPUT_PATH"]
    output_path = os.environ["OUTPUT_PATH"]

    df = pd.read_csv(input_path)
    result = compute_utilization(df)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()

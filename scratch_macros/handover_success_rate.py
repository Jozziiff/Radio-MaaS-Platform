"""handover-success-rate macro (M6): flags cells with degraded LTE
inter-cell handover success.

Uses direct df["col"] vectorized access (np.where-free, plain pandas
arithmetic on whole columns) -- a different access pattern than
rtwp-anomaly-demo's row["..."] iterrows() loop, exercising the AST
engine's subscript-read detection on bare df[...] reads instead.

Computed columns (success_rate, status) are kept as local variables
and only assigned into `result` once, never read back via
result["..."] afterward -- ast_engine.py's column detection can't tell
a read of a freshly-computed column apart from a read of a real input
column (both are just `name["string"]` on a bare Name), so reading
result["success_rate"] back would have made it show up as a required
*input* column too.

"""

import os

import pandas as pd

SUCCESS_RATE_THRESHOLD = 95.0


def compute_success_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Compute handover success rate per cell and flag degraded ones.

    Args:
        df: DataFrame with cell_id, handover_attempts, handover_successes.

    Returns:
        A new DataFrame with cell_id, handover_attempts, handover_successes,
        success_rate, status.
    """
    success_rate = df["handover_successes"] / df["handover_attempts"] * 100
    status = success_rate.apply(
        lambda rate: "degraded" if rate < SUCCESS_RATE_THRESHOLD else "ok"
    )
    result = pd.DataFrame()
    result["cell_id"] = df["cell_id"]
    result["handover_attempts"] = df["handover_attempts"]
    result["handover_successes"] = df["handover_successes"]
    result["success_rate"] = success_rate
    result["status"] = status
    return result


def main() -> None:
    """Read INPUT_PATH, compute handover success rates, write the result to OUTPUT_PATH."""
    input_path = os.environ["INPUT_PATH"]
    output_path = os.environ["OUTPUT_PATH"]

    df = pd.read_csv(input_path)
    result = compute_success_rate(df)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()

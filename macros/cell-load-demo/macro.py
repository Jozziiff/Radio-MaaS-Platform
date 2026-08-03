"""cell-load-demo macro (M1): flags cells over a fixed load threshold.

Deliberately simple: this macro exists to exercise the M1 pipeline
(hand-written Dockerfile -> Kubernetes Job -> local file I/O), not to
perform a realistic radio analysis.
"""

import os

import pandas as pd

LOAD_THRESHOLD_PERCENT = 80.0


def flag_high_load(df: pd.DataFrame) -> pd.DataFrame:
    """Mark each row "overload" if load_percent exceeds the threshold, else "ok".

    Args:
        df: DataFrame with at least a `load_percent` column.

    Returns:
        A copy of `df` with a new `status` column appended.
    """
    result = df.copy()
    result["status"] = result["load_percent"].apply(
        lambda load: "overload" if load > LOAD_THRESHOLD_PERCENT else "ok"
    )
    return result


def main() -> None:
    """Read INPUT_PATH, flag high-load rows, write the result to OUTPUT_PATH."""
    input_path = os.environ["INPUT_PATH"]
    output_path = os.environ["OUTPUT_PATH"]

    df = pd.read_csv(input_path)
    result = flag_high_load(df)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()

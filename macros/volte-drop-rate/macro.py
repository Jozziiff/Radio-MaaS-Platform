"""volte-drop-rate macro (M6): flags cells with a critical VoLTE call
drop rate.

Uses row["..."] inside a df.iterrows() loop, same access pattern family
as rtwp-anomaly-demo -- kept here deliberately as the second of three
varying styles requested for this batch (direct df[...], iterrows(),
.loc), not because it needed to match rtwp-anomaly-demo specifically.
"""

import os

import pandas as pd

DROP_RATE_CRITICAL_THRESHOLD = 2.0


def compute_drop_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Compute VoLTE call drop rate per cell and flag critical ones.

    Args:
        df: DataFrame with cell_id, volte_calls, volte_drops.

    Returns:
        A new DataFrame with cell_id, volte_calls, volte_drops,
        drop_rate_percent, status.
    """
    records = []
    for _, row in df.iterrows():
        cell_id = row["cell_id"]
        volte_calls = row["volte_calls"]
        volte_drops = row["volte_drops"]
        drop_rate_percent = volte_drops / volte_calls * 100
        status = "critical" if drop_rate_percent > DROP_RATE_CRITICAL_THRESHOLD else "ok"
        records.append(
            {
                "cell_id": cell_id,
                "volte_calls": volte_calls,
                "volte_drops": volte_drops,
                "drop_rate_percent": drop_rate_percent,
                "status": status,
            }
        )
    return pd.DataFrame(records)


def main() -> None:
    """Read INPUT_PATH, compute VoLTE drop rates, write the result to OUTPUT_PATH."""
    input_path = os.environ["INPUT_PATH"]
    output_path = os.environ["OUTPUT_PATH"]

    df = pd.read_csv(input_path)
    result = compute_drop_rate(df)
    result.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()

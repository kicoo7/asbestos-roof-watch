"""Append-only per-roof per-scene observation table (PLAN 5, observations.parquet)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

KEY = ["roof_id", "scene_id"]


def load_observations(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def append_observations(path: Path, new: pd.DataFrame) -> int:
    """Add rows not already stored (keyed by roof_id + scene_id). Returns rows added."""
    path = Path(path)
    old = load_observations(path)
    if not old.empty:
        known = pd.MultiIndex.from_frame(old[KEY])
        new = new[~pd.MultiIndex.from_frame(new[KEY]).isin(known)]
    new = new.drop_duplicates(KEY)
    if new.empty:
        return 0
    out = pd.concat([old, new], ignore_index=True) if not old.empty else new.reset_index(drop=True)
    tmp = path.with_suffix(".tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(path)
    return len(new)

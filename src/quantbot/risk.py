from __future__ import annotations

import pandas as pd


def constrain_weights(weights: pd.DataFrame, max_position: float, max_gross: float, max_industry: float, industries: dict[str, str] | None = None) -> pd.DataFrame:
    out = weights.clip(lower=-max_position, upper=max_position).copy()
    if industries:
        for date, row in out.iterrows():
            for industry in set(industries.values()):
                members = [s for s in out.columns if industries.get(s) == industry]
                exposure = row[members].abs().sum()
                if exposure > max_industry:
                    out.loc[date, members] *= max_industry / exposure
    gross = out.abs().sum(axis=1)
    scale = (max_gross / gross).clip(upper=1.0).fillna(1.0)
    return out.mul(scale, axis=0)

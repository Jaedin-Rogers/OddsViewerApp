"""Checks predict_player.py's feature builder against the ml views on already-played games.

Run: python validate_predict.py [n_games]
Compares every view-derived feature (ml.player_rolling_features + ml.team_opponent_context) and reports the
model's MAE on the sample versus the L10-average baseline. Slow (the ml views take ~20 s each to query).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predict_player import Predictor  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
START, END = "2025-12-01", "2026-03-15"

p = Predictor()
with p.engine.connect() as conn:
    roll = pd.read_sql(text(
        "SELECT * FROM ml.player_rolling_features WHERE gamedatetimeest BETWEEN :a AND :b AND points_avg_l10 IS NOT NULL"),
        conn, params={"a": START, "b": END})
    ctx = pd.read_sql(text(
        "SELECT * FROM ml.team_opponent_context WHERE gamedatetimeest BETWEEN :a AND :b"), conn, params={"a": START, "b": END})

ctx = ctx.drop(columns=["playerteamid", "opponentteamid", "gamedatetimeest"])
sample = roll.merge(ctx, on=["gameid", "playerid"]).sample(N, random_state=1)

compare = [c for c in p.features if c in roll.columns or c in ctx.columns]
diffs, preds = [], []
for row in sample.itertuples():
    own, opp = int(row.playerteamid), int(row.opponentteamid)
    as_of = pd.Timestamp(row.gamedatetimeest)
    pl, tl = p.load_context([own, opp], as_of)
    rows = p.load_player_rows(int(row.playerindex), as_of)
    feats, _ = p.build_features(int(row.playerindex), own, opp, bool(row.home == 1), as_of, pl, tl, rows)
    diffs.append({c: (abs(feats[c] - getattr(row, c)) if pd.notna(getattr(row, c)) and pd.notna(feats[c])
                      else (0.0 if pd.isna(getattr(row, c)) and pd.isna(feats[c]) else np.nan)) for c in compare})
    rec = {"l10_points": row.points_avg_l10, "actual": row.points}
    rec["model"] = p.predict_row(feats, "points")[0]
    preds.append(rec)

d = pd.DataFrame(diffs)
report = pd.DataFrame({"max_abs_diff": d.max(), "n_exact": (d.fillna(1e9) < 1e-6).sum(), "n_nan_mismatch": d.isna().sum()})
print(f"Compared {len(sample)} games, {len(compare)} view-derived features")
print(report[report.max_abs_diff.fillna(1e9) > 1e-6].round(4).to_string() or "all features match")
r = pd.DataFrame(preds)
print(f"\npoints MAE  model={np.abs(r.model - r.actual).mean():.2f}  l10_avg={np.abs(r.l10_points - r.actual).mean():.2f}")

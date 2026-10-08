import re
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from util import get_engine  # noqa: E402

engine = get_engine()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WARMUP_SEASON = 2021                      # loaded only so team/position rolling stats are warm by 2022
TRAIN_SEASONS = [2022, 2023]
TEST_SEASONS = [2024, 2025]
TARGETS = {"points": "points", "assists": "assists", "rebounds": "reboundstotal"}

# ---------------------------------------------------------------------------
# 1. Load the ml views
#    ml.player_rolling_features only contains games the player played (numMinutes > 0).
# ---------------------------------------------------------------------------
SINCE = f"{WARMUP_SEASON}-08-01"
with engine.connect() as conn:
    roll = pd.read_sql(text("SELECT * FROM ml.player_rolling_features WHERE gamedatetimeest >= :d"), conn, params={"d": SINCE})
    ctx = pd.read_sql(text("SELECT * FROM ml.team_opponent_context WHERE gamedatetimeest >= :d"), conn, params={"d": SINCE})
    dim = pd.read_sql(text(
        'SELECT playerindex, guard, forward, center, "heightInches" AS height_in, "bodyWeightLbs" AS weight_lbs '
        "FROM play.player_dim"
    ), conn)

null_ids = roll[["playerid", "playerteamid", "opponentteamid"]].isna().sum()
assert null_ids.sum() == 0, f"Null ids in ml views {null_ids.to_dict()}; re-run the updated ml.player_game_base view."
assert (roll["numminutes"] > 0).all(), "DNP rows found; re-run the updated ml.player_rolling_features view."

ctx_cols = [c for c in ctx.columns if c not in ("gameid", "playerid", "playerteamid", "opponentteamid", "gamedatetimeest")]
df = roll.merge(ctx[["gameid", "playerid", *ctx_cols]], on=["gameid", "playerid"], how="left", validate="one_to_one")
df = df.merge(dim, on="playerindex", how="left")
df = df.sort_values(["gamedatetimeest", "gameid"]).reset_index(drop=True)

# ---------------------------------------------------------------------------
# 2. Position group (C > F > G from player_dim flags; fallback is the most common starting position
#    seen up to the end of the training seasons, so no test-period games inform it)
# ---------------------------------------------------------------------------
starter_pos = (
    df[df.startingposition.isin(["G", "F", "C"]) & (df.season_start_year <= max(TRAIN_SEASONS))]
    .groupby("playerindex").startingposition.agg(lambda s: s.mode().iat[0])
)
df["pos"] = np.select([df.center == 1, df.forward == 1, df.guard == 1], ["C", "F", "G"], default="U")
df["pos"] = df["pos"].where(df["pos"] != "U", df["playerindex"].map(starter_pos)).fillna("U")

# ---------------------------------------------------------------------------
# 3. Team strength: own team vs opposing team (rolling margin and win rate, prior games only)
# ---------------------------------------------------------------------------
tg = (
    df.groupby(["gameid", "playerteamid"], as_index=False)
    .agg(date=("gamedatetimeest", "min"), season=("season_start_year", "first"), margin=("game_margin", "first"))
    .sort_values("date")
)
tg["won"] = (tg["margin"] > 0).astype(float)
by_team = tg.groupby("playerteamid")
tg["net_l10"] = by_team["margin"].transform(lambda s: s.shift(1).rolling(10, min_periods=3).mean())
tg["win_pct_l10"] = by_team["won"].transform(lambda s: s.shift(1).rolling(10, min_periods=3).mean())
tg["net_season"] = tg.groupby(["playerteamid", "season"])["margin"].transform(
    lambda s: s.shift(1).expanding(min_periods=3).mean()
)

strength = ["net_l10", "win_pct_l10", "net_season"]
own = tg[["gameid", "playerteamid", *strength]].rename(columns={c: f"team_{c}" for c in strength})
opp = tg[["gameid", "playerteamid", *strength]].rename(
    columns={"playerteamid": "opponentteamid", **{c: f"opp_{c}" for c in strength}}
)
df = df.merge(own, on=["gameid", "playerteamid"], how="left").merge(opp, on=["gameid", "opponentteamid"], how="left")
df["strength_gap_l10"] = df["team_net_l10"] - df["opp_net_l10"]

# ---------------------------------------------------------------------------
# 4. Opponent defense vs this player's position (production allowed to the position, rolling L10)
# ---------------------------------------------------------------------------
pos_game = (
    # U positions are excluded from this analysis
    df[df.pos != "U"]
    .groupby(["gameid", "opponentteamid", "pos"], as_index=False)
    .agg(date=("gamedatetimeest", "min"), pts=("points", "sum"), reb=("reboundstotal", "sum"), ast=("assists", "sum"))
    .sort_values("date")
)
by_def = pos_game.groupby(["opponentteamid", "pos"])
# c would be one of the columns representing points, rebounds, or assists
for c in ["pts", "reb", "ast"]:
    pos_game[f"opp_pos_allowed_{c}_l10"] = by_def[c].transform(lambda s: s.shift(1).rolling(10, min_periods=3).mean())
df = df.merge(
    pos_game.drop(columns=["date", "pts", "reb", "ast"]), on=["gameid", "opponentteamid", "pos"], how="left"
)

# ---------------------------------------------------------------------------
# 5. Positional matchup: opposing player at the same position with the most expected minutes.
#    Candidates come from that game's box score (who played), so for live inference substitute the
#    expected starter/rotation player.
# ---------------------------------------------------------------------------
match_cols = [
    "minutes_avg_l10", "points_avg_season", "rebounds_avg_season", "assists_avg_season",
    "blocks_avg_l3", "steals_avg_l3", "height_in", "weight_lbs",
]
matchup = (
    df[df.pos != "U"]
    .sort_values("minutes_avg_l10", ascending=False)
    .drop_duplicates(["gameid", "playerteamid", "pos"])[["gameid", "playerteamid", "pos", *match_cols]]
    .rename(columns={"playerteamid": "opponentteamid", **{c: f"opp_match_{c}" for c in match_cols}})
)
df = df.merge(matchup, on=["gameid", "opponentteamid", "pos"], how="left")
df["opp_match_height_diff"] = df["height_in"] - df["opp_match_height_in"]

# ---------------------------------------------------------------------------
# 6. Context flags and feature list (pre-game information only)
# ---------------------------------------------------------------------------
df["season"] = df["season_start_year"]
df["is_playoff"] = (df["gametype"] == "Playoffs").astype(int)
df["games_before_in_season"] = df.groupby(["playerindex", "season"]).cumcount()
df = pd.concat([df, pd.get_dummies(df["pos"], prefix="pos", dtype=int)], axis=1)

ROLLING_RE = re.compile(r"_(prev|avg_l\d+|std_l\d+|avg_season|std_season)$")
feature_cols = list(dict.fromkeys(
    [c for c in roll.columns if ROLLING_RE.search(c)]
    + ctx_cols
    + [c for c in df.columns if c.startswith(("team_net", "team_win", "opp_net", "opp_win", "opp_pos_allowed", "opp_match_", "pos_"))]
    + ["strength_gap_l10", "days_since_previous_game", "home", "is_playoff", "games_before_in_season", "height_in", "weight_lbs"]
))

CURRENT_GAME_COLS = {
    "points", "assists", "reboundstotal", "reboundsoffensive", "reboundsdefensive", "steals", "blocks", "turnovers",
    "plusminus", "numminutes", "fieldgoalsmade", "fieldgoalsattempted", "threepointersmade", "threepointersattempted",
    "freethrowsmade", "freethrowsattempted", "win", "homescore", "awayscore", "winner", "game_margin",
    "player_team_score", "opponent_score",
}
assert not CURRENT_GAME_COLS & set(feature_cols), CURRENT_GAME_COLS & set(feature_cols)


# ---------------------------------------------------------------------------
# 7. Leakage audit: recompute features from raw games strictly before each row and compare
# ---------------------------------------------------------------------------
def audit_no_leakage(frame, team_games, n=300, seed=0):
    assert not frame.duplicated(["playerid", "gamedatetimeest"]).any(), "ties break window ordering"
    sample = frame[frame["points_avg_l10"].notna()].sample(min(n, len(frame)), random_state=seed)
    hist = {pid: g.sort_values("gamedatetimeest") for pid, g in frame[frame.playerid.isin(sample.playerid)].groupby("playerid")}
    checked = 0
    
    for _, row in sample.iterrows():
        prior = hist[row.playerid][lambda g: g.gamedatetimeest < row.gamedatetimeest]
        team_prior = team_games[(team_games.playerteamid == row.playerteamid) & (team_games.date < row.gamedatetimeest)]
        if len(prior) < 10 or len(team_prior) < 10:
            continue  # earlier history sits before the warm-up cutoff, so the window can't be rebuilt
        season_prior = prior[prior.season_start_year == row.season_start_year]
        expected = {
            "points_avg_l5": prior.points.tail(5).mean(),
            "points_avg_l10": prior.points.tail(10).mean(),
            "points_avg_season": season_prior.points.mean() if len(season_prior) else np.nan,
            "team_net_l10": team_prior.margin.tail(10).mean(),
        }
        for col, exp in expected.items():
            assert np.isclose(row[col], exp, equal_nan=True), (
                f"{col}: player {row.playerid} on {row.gamedatetimeest}: view/pandas={row[col]} recomputed={exp}"
            )
        checked += 1
    print(f"leakage audit passed on {checked} sampled rows (played games only, current game excluded)")


audit_no_leakage(df, tg)

# Train/test rows: players with enough history for the L10 window.
model_df = df[df["points_avg_l10"].notna()]
train = model_df[model_df.season.isin(TRAIN_SEASONS)]
test = model_df[model_df.season.isin(TEST_SEASONS)]
print(f"train={len(train):,}  test={len(test):,}  features={len(feature_cols)}")

# ---------------------------------------------------------------------------
# 8. Train: Poisson-loss gradient boosting per stat, plus a negative-binomial dispersion
#    estimated on the last training season (model fit on the earlier seasons only).
# ---------------------------------------------------------------------------
def make_model():
    return HistGradientBoostingRegressor(
        loss="poisson", learning_rate=0.05, max_iter=400, max_leaf_nodes=31,
        min_samples_leaf=50, l2_regularization=1.0, random_state=42,
    )


def nb_alpha(y, mu):
    return max(0.0, float(np.sum((y - mu) ** 2 - mu) / np.sum(mu ** 2)))


calib_season = max(TRAIN_SEASONS)
models, alphas = {}, {}
for name, col in TARGETS.items():
    early, late = train[train.season < calib_season], train[train.season == calib_season]
    probe = make_model().fit(early[feature_cols], early[col])
    alphas[name] = nb_alpha(late[col].to_numpy(), probe.predict(late[feature_cols]))
    models[name] = make_model().fit(train[feature_cols], train[col])
print("dispersion alpha:", {k: round(v, 3) for k, v in alphas.items()})


# ---------------------------------------------------------------------------
# 9. Predict any line: P(stat > line) from the predicted mean and fitted dispersion
# ---------------------------------------------------------------------------
def prob_over(mu, line, alpha):
    mu, k = np.asarray(mu, dtype=float), np.floor(line)
    if alpha < 1e-6:
        return poisson.sf(k, mu)
    n = 1.0 / alpha
    return nbinom.sf(k, n, n / (n + mu))


def predict_stats(frame):
    return pd.DataFrame({f"pred_{n}": models[n].predict(frame[feature_cols]) for n in TARGETS}, index=frame.index)


# ---------------------------------------------------------------------------
# 10. Evaluate on 2024-2025: error vs naive baselines, and calibration of P(over) on proxy lines
#     (proxy line = L10 average rounded down to x.5, until real sportsbook lines are ingested)
# ---------------------------------------------------------------------------
def evaluate(split):
    rows = []
    for name, col in TARGETS.items():
        for season, part in split.groupby("season"):
            part = part.dropna(subset=[f"{name}_avg_l10", f"{name}_avg_season"])
            y, mu = part[col].to_numpy(), models[name].predict(part[feature_cols])
            for label, pred in [("model", mu), ("l10_avg", part[f"{name}_avg_l10"]), ("season_avg", part[f"{name}_avg_season"])]:
                rows.append({
                    "stat": name, "season": season, "predictor": label,
                    "mae": mean_absolute_error(y, pred),
                    "rmse": mean_squared_error(y, pred) ** 0.5,
                    "bias": float(np.mean(pred - y)),
                })
    return pd.DataFrame(rows).pivot_table(index=["stat", "season"], columns="predictor", values=["mae", "rmse", "bias"]).round(3)


def calibration(split, name, bins=10):
    part = split.dropna(subset=[f"{name}_avg_l10"])
    line = np.floor(part[f"{name}_avg_l10"]) + 0.5
    p = prob_over(models[name].predict(part[feature_cols]), line.to_numpy(), alphas[name])
    hit = (part[TARGETS[name]] > line).astype(float).to_numpy()
    out = pd.DataFrame({"p": p, "hit": hit})
    out["bucket"] = pd.qcut(out["p"], bins, duplicates="drop")
    table = out.groupby("bucket", observed=True).agg(pred=("p", "mean"), actual=("hit", "mean"), n=("hit", "size"))
    return table, float(np.mean((p - hit) ** 2))  # reliability table, Brier score


print(evaluate(test))
for stat in TARGETS:
    table, brier = calibration(test, stat)
    print(f"\n{stat}: Brier={brier:.4f}")
    print(table.round(3))

# ---------------------------------------------------------------------------
# 11. Persist for the serving layer
# ---------------------------------------------------------------------------
MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
joblib.dump({"models": models, "alphas": alphas, "features": feature_cols}, MODEL_DIR / "player_stat_models.joblib")

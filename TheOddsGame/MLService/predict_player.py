"""Serving layer for the player stat models trained by train_player.py.

Builds the pre-game feature vector for an *upcoming* game (the ml views only describe games that
already have a row) and returns the predicted mean plus P(over/under) for a sportsbook line.

Usage (one JSON array in, one JSON array out):
    echo '[{"player":"Luka Doncic","market":"player_points","line":28.5,
            "home_team":"Los Angeles Lakers","away_team":"Boston Celtics",
            "match_date":"2026-11-02T00:30:00Z"}]' | python predict_player.py
"""
import json
import re
import sys
import unicodedata
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson
from sqlalchemy import bindparam, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from util import get_engine  # noqa: E402

MODEL_PATH = Path(__file__).resolve().parent / "models" / "player_stat_models.joblib"

MARKET_TO_STAT = {
    "player_points": "points",
    "player_points_alternate": "points",
    "player_rebounds": "rebounds",
    "player_rebounds_alternate": "rebounds",
    "player_assists": "assists",
    "player_assists_alternate": "assists",
}
STAT_COLUMN = {"points": "points", "rebounds": "rebounds", "assists": "assists"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
EASTERN = ZoneInfo("America/New_York")

_ASCII_FOLD = str.maketrans({
    "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE", "ß": "ss", "ı": "i", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D",
})


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def norm_name(value) -> str:
    folded = unicodedata.normalize("NFKD", str(value).translate(_ASCII_FOLD))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", folded).split() if t not in NAME_SUFFIXES]
    return " ".join(tokens)


def season_of(ts) -> int:
    return ts.year - (1 if ts.month < 10 else 0)


def before(frame, as_of):
    """Rows on an earlier calendar day than as_of. Day granularity avoids counting the current game when
    two tables store different times (or midnight) for the same game."""
    return frame.ts.dt.normalize() < as_of.normalize()


def prob_over(mu, line, alpha):
    k = np.floor(line)
    if alpha < 1e-6:
        return float(poisson.sf(k, mu))
    n = 1.0 / alpha
    return float(nbinom.sf(k, n, n / (n + mu)))


class Predictor:
    def __init__(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            artifact = joblib.load(MODEL_PATH)
        self.models = artifact["models"]
        self.alphas = artifact["alphas"]
        self.features = artifact["features"]
        self.engine = get_engine()
        with self.engine.connect() as conn:
            self.dim = pd.read_sql(text(
                'SELECT playerindex, "firstName" AS first_name, "lastName" AS last_name, guard, forward, center, '
                '"heightInches" AS height_in, "bodyWeightLbs" AS weight_lbs, "nbaFlag" AS nba_flag, "toYear" AS to_year '
                "FROM play.player_dim"), conn)
            self.teams = pd.read_sql(text(
                'SELECT "teamId" AS teamid, "teamCity" AS city, "teamName" AS name, "teamAbbrev" AS abbrev FROM team.team_dim'), conn)
        self.dim["key"] = (self.dim.first_name.fillna("") + " " + self.dim.last_name.fillna("")).map(norm_name)
        self.dim_by_id = self.dim.drop_duplicates("playerindex").set_index("playerindex")
        self._context_cache = {}

    # ------------------------------------------------------------------ lookups
    def find_player(self, name):
        key = norm_name(name)
        hits = self.dim[self.dim.key == key]
        if hits.empty:
            return None
        hits = hits.assign(_to=hits.to_year.fillna(9999)).sort_values(["nba_flag", "_to"], ascending=False)
        return int(hits.iloc[0].playerindex)

    def find_team(self, name):
        key = norm_name(name)
        for row in self.teams.itertuples():
            full = norm_name(f"{row.city} {row.name}")
            if key == full or key.endswith(norm_name(row.name)) or key == norm_name(row.abbrev):
                return int(row.teamid)
        return None

    # --------------------------------------------------------------- data loading
    def load_context(self, team_ids, as_of):
        season = season_of(as_of)
        key = (tuple(sorted(team_ids)), season)
        if key in self._context_cache:
            return self._context_cache[key]

        since = f"{season - 1}-08-01"
        pl_sql = text("""
            SELECT pl."gameId" AS gameid, pl.playerindex AS playerindex,
                   ml.parse_game_ts(pl."gameDateTimeEst") AS ts, pl."gameType" AS gametype, pl.home AS home,
                   pl."startingPosition" AS startingposition, pl."numMinutes"::numeric AS minutes,
                   pl.points AS points, pl.assists AS assists, pl."reboundsTotal" AS rebounds,
                   pl.steals AS steals, pl.blocks AS blocks, pl.turnovers AS turnovers,
                   pl.playerteamid::bigint AS teamid, pl.opponentteamid::bigint AS oppid
            FROM play.player_log pl JOIN game.game_dim g ON g."gameId" = pl."gameId"
            WHERE g."gameDate" >= :since AND (pl.playerteamid IN :teams OR pl.opponentteamid IN :teams)
        """).bindparams(bindparam("teams", expanding=True))
        tl_sql = text("""
            SELECT tl."gameId" AS gameid, tl."teamId" AS teamid, ml.parse_game_ts(tl."gameDateTimeEst") AS ts,
                   tl."teamScore" AS team_score, tl."opponentScore" AS opp_score,
                   tl."fieldGoalsPercentage" AS fg_pct, tl."threePointersPercentage" AS fg3_pct,
                   tl."reboundsTotal" AS rebounds, tl.assists AS assists, tl.turnovers AS turnovers,
                   tl.steals AS steals, tl.blocks AS blocks, tl."reboundsDefensive" AS dreb
            FROM team.team_log tl JOIN game.game_dim g ON g."gameId" = tl."gameId"
            WHERE g."gameDate" >= :since AND tl."teamId" IN :teams
        """).bindparams(bindparam("teams", expanding=True))
        params = {"since": since, "teams": list(team_ids)}
        with self.engine.connect() as conn:
            pl = pd.read_sql(pl_sql, conn, params=params)
            tl = pd.read_sql(tl_sql, conn, params=params)
        pl = self._clean_player_rows(pl)
        for c in tl.columns:
            if c != "ts":
                tl[c] = pd.to_numeric(tl[c], errors="coerce")
        tl["ts"] = pd.to_datetime(tl["ts"])
        tl["season"] = tl.ts.dt.year - (tl.ts.dt.month < 10).astype(int)
        self._context_cache[key] = (pl, tl)
        return pl, tl

    def load_player_rows(self, pid, as_of):
        since = f"{season_of(as_of) - 1}-08-01"
        sql = text("""
            SELECT pl."gameId" AS gameid, pl.playerindex AS playerindex,
                   ml.parse_game_ts(pl."gameDateTimeEst") AS ts, pl."gameType" AS gametype, pl.home AS home,
                   pl."startingPosition" AS startingposition, pl."numMinutes"::numeric AS minutes,
                   pl.points AS points, pl.assists AS assists, pl."reboundsTotal" AS rebounds,
                   pl.steals AS steals, pl.blocks AS blocks, pl.turnovers AS turnovers,
                   pl.playerteamid::bigint AS teamid, pl.opponentteamid::bigint AS oppid
            FROM play.player_log pl JOIN game.game_dim g ON g."gameId" = pl."gameId"
            WHERE g."gameDate" >= :since AND pl.playerindex = :pid
        """)
        with self.engine.connect() as conn:
            return self._clean_player_rows(pd.read_sql(sql, conn, params={"since": since, "pid": pid}))

    @staticmethod
    def _clean_player_rows(pl):
        for c in ["home", "minutes", "points", "assists", "rebounds", "steals", "blocks", "turnovers", "teamid", "oppid", "playerindex"]:
            pl[c] = pd.to_numeric(pl[c], errors="coerce")
        pl["ts"] = pd.to_datetime(pl["ts"])
        pl["season"] = pl.ts.dt.year - (pl.ts.dt.month < 10).astype(int)
        return pl

    # ----------------------------------------------------------- feature builders
    def position_map(self, pl):
        starter = (
            pl[pl.startingposition.isin(["G", "F", "C"])]
            .groupby("playerindex").startingposition.agg(lambda s: s.mode().iat[0])
        )

        def pos_for(pid):
            d = self.dim_by_id.loc[pid] if pid in self.dim_by_id.index else None
            if d is not None:
                if d.center == 1: return "C"
                if d.forward == 1: return "F"
                if d.guard == 1: return "G"
            return starter.get(pid, "U")

        return {pid: pos_for(pid) for pid in pl.playerindex.dropna().unique()}

    @staticmethod
    def player_rolling(h, as_of):
        """Rolling features from a player's played games strictly before as_of (matches ml.player_rolling_features)."""
        out = {}
        season_h = h[h.season == season_of(as_of)]
        last = lambda s: s.iloc[-1] if len(s) else np.nan
        avg = lambda s, n: s.tail(n).mean() if len(s) else np.nan
        std = lambda s, n: s.tail(n).std(ddof=1) if len(s.tail(n)) > 1 else np.nan
        for stat in ["points", "assists", "rebounds", "steals", "blocks", "turnovers", "minutes"]:
            out[f"{stat}_prev"] = last(h[stat])
        for stat in ["points", "assists", "rebounds", "steals", "blocks"]:
            out[f"{stat}_avg_l3"] = avg(h[stat], 3)
        for stat in ["points", "assists", "rebounds"]:
            for n in (5, 10):
                out[f"{stat}_avg_l{n}"] = avg(h[stat], n)
                out[f"{stat}_std_l{n}"] = std(h[stat], n)
            out[f"{stat}_avg_season"] = season_h[stat].mean() if len(season_h) else np.nan
            out[f"{stat}_std_season"] = season_h[stat].std(ddof=1) if len(season_h) > 1 else np.nan
        out["minutes_avg_l5"] = avg(h.minutes, 5)
        out["minutes_avg_l10"] = avg(h.minutes, 10)
        return out

    @staticmethod
    def team_strength(t, as_of):
        t = t[before(t, as_of)].sort_values(["ts", "gameid"])
        margin = (t.team_score - t.opp_score)
        won = (margin > 0).astype(float)
        sm = margin[t.season == season_of(as_of)]
        return {
            "net_l10": margin.tail(10).mean() if len(margin) >= 3 else np.nan,
            "win_pct_l10": won.tail(10).mean() if len(won) >= 3 else np.nan,
            "net_season": sm.mean() if len(sm) >= 3 else np.nan,
        }

    @staticmethod
    def team_ctx(t, as_of):
        t = t[before(t, as_of)].sort_values(["ts", "gameid"])
        l5, l10 = t.tail(5), t.tail(10)
        return {
            "opp_scored_allowed_l5": l5.opp_score.mean(), "opp_scored_allowed_l10": l10.opp_score.mean(),
            "points_scored_l5": l5.team_score.mean(), "points_scored_l10": l10.team_score.mean(),
            "fg_pct_l5": l5.fg_pct.mean(), "fg3_pct_l5": l5.fg3_pct.mean(), "rebounds_l5": l5.rebounds.mean(),
            "assists_l5": l5.assists.mean(), "turnovers_l5": l5.turnovers.mean(), "steals_l5": l5.steals.mean(),
            "blocks_l5": l5.blocks.mean(), "dreb_l5": l5.dreb.mean(),
        }

    def build_features(self, pid, own_team, opp_team, is_home, as_of, pl, tl, player_rows, is_playoff=0):
        """Feature dict for one player in one game. pl/tl hold both teams' recent logs; player_rows is the player's own log."""
        played = player_rows[(player_rows.minutes > 0) & before(player_rows, as_of)].sort_values(["ts", "gameid"])
        if played.empty:
            raise ValueError("No prior played games for this player.")
        feats = self.player_rolling(played, as_of)

        posmap = self.position_map(pd.concat([pl, player_rows], ignore_index=True))
        pos = posmap.get(pid, "U")
        dim = self.dim_by_id.loc[pid] if pid in self.dim_by_id.index else None
        height = dim.height_in if dim is not None else np.nan
        weight = dim.weight_lbs if dim is not None else np.nan

        own_ctx = self.team_ctx(tl[tl.teamid == own_team], as_of)
        opp_ctx = self.team_ctx(tl[tl.teamid == opp_team], as_of)
        own_str = self.team_strength(tl[tl.teamid == own_team], as_of)
        opp_str = self.team_strength(tl[tl.teamid == opp_team], as_of)

        feats.update({
            "opponent_points_allowed_l5": opp_ctx["opp_scored_allowed_l5"],
            "opponent_points_allowed_l10": opp_ctx["opp_scored_allowed_l10"],
            "team_fg_pct_l5": opp_ctx["fg_pct_l5"], "team_3p_pct_l5": opp_ctx["fg3_pct_l5"],
            "team_rebounds_l5": opp_ctx["rebounds_l5"], "team_assists_l5": opp_ctx["assists_l5"],
            "team_turnovers_l5": opp_ctx["turnovers_l5"], "team_steals_l5": opp_ctx["steals_l5"],
            "team_blocks_l5": opp_ctx["blocks_l5"], "defensive_rebounds_l5": opp_ctx["dreb_l5"],
            "team_points_scored_l5": opp_ctx["points_scored_l5"], "team_points_scored_l10": opp_ctx["points_scored_l10"],
            "player_team_points_scored_l5": own_ctx["points_scored_l5"],
            "player_team_points_scored_l10": own_ctx["points_scored_l10"],
            "player_team_fg_pct_l5": own_ctx["fg_pct_l5"], "player_team_3p_pct_l5": own_ctx["fg3_pct_l5"],
            "player_team_rebounds_l5": own_ctx["rebounds_l5"], "player_team_assists_l5": own_ctx["assists_l5"],
            "player_team_turnovers_l5": own_ctx["turnovers_l5"],
            "team_net_l10": own_str["net_l10"], "team_win_pct_l10": own_str["win_pct_l10"], "team_net_season": own_str["net_season"],
            "opp_net_l10": opp_str["net_l10"], "opp_win_pct_l10": opp_str["win_pct_l10"], "opp_net_season": opp_str["net_season"],
            "strength_gap_l10": own_str["net_l10"] - opp_str["net_l10"],
        })

        # Production the opponent allowed to this position over its last 10 games.
        faced = pl[(pl.oppid == opp_team) & (pl.minutes > 0) & before(pl, as_of)].copy()
        faced["pos"] = faced.playerindex.map(posmap)
        pos_games = (
            faced[faced.pos == pos].groupby("gameid")
            .agg(ts=("ts", "min"), pts=("points", "sum"), reb=("rebounds", "sum"), ast=("assists", "sum"))
            .sort_values("ts").tail(10)
        ) if pos != "U" else faced.iloc[0:0].groupby("gameid").size().to_frame()
        for c in ("pts", "reb", "ast"):
            feats[f"opp_pos_allowed_{c}_l10"] = pos_games[c].mean() if pos != "U" and len(pos_games) >= 3 else np.nan

        # Expected positional matchup: the opponent's same-position player with the most recent minutes.
        match = self.expected_matchup(pl, opp_team, pos, posmap, as_of)
        for k in ["minutes_avg_l10", "points_avg_season", "rebounds_avg_season", "assists_avg_season",
                  "blocks_avg_l3", "steals_avg_l3", "height_in", "weight_lbs"]:
            feats[f"opp_match_{k}"] = match.get(k, np.nan)
        feats["opp_match_height_diff"] = height - match.get("height_in", np.nan)

        season_h = played[played.season == season_of(as_of)]
        prior_any = player_rows[before(player_rows, as_of)].ts
        feats.update({
            "pos_C": int(pos == "C"), "pos_F": int(pos == "F"), "pos_G": int(pos == "G"),
            "days_since_previous_game": (as_of - prior_any.max()).total_seconds() / 86400.0 if len(prior_any) else np.nan,
            "home": int(is_home), "is_playoff": int(is_playoff),
            "games_before_in_season": len(season_h), "height_in": height, "weight_lbs": weight,
        })
        return feats, pos

    def expected_matchup(self, pl, opp_team, pos, posmap, as_of):
        if pos == "U":
            return {}
        opp_rows = pl[(pl.teamid == opp_team) & (pl.minutes > 0) & before(pl, as_of)]
        recent_games = opp_rows.groupby("gameid").ts.min().sort_values().tail(3).index
        pool = opp_rows[opp_rows.gameid.isin(recent_games)].playerindex.unique()
        best, best_min = {}, -1.0
        for cand in pool:
            if posmap.get(cand) != pos:
                continue
            h = opp_rows[opp_rows.playerindex == cand].sort_values(["ts", "gameid"])
            m10 = h.minutes.tail(10).mean()
            if m10 > best_min:
                sh = h[h.season == season_of(as_of)]
                d = self.dim_by_id.loc[cand] if cand in self.dim_by_id.index else None
                best_min = m10
                best = {
                    "minutes_avg_l10": m10,
                    "points_avg_season": sh.points.mean() if len(sh) else np.nan,
                    "rebounds_avg_season": sh.rebounds.mean() if len(sh) else np.nan,
                    "assists_avg_season": sh.assists.mean() if len(sh) else np.nan,
                    "blocks_avg_l3": h.blocks.tail(3).mean(), "steals_avg_l3": h.steals.tail(3).mean(),
                    "height_in": d.height_in if d is not None else np.nan,
                    "weight_lbs": d.weight_lbs if d is not None else np.nan,
                }
        return best

    # ------------------------------------------------------------------ prediction
    def predict_row(self, feats, stat):
        frame = pd.DataFrame([{c: feats.get(c, np.nan) for c in self.features}], columns=self.features).astype(float)
        mu = float(self.models[stat].predict(frame)[0])
        return mu, frame

    def predict_request(self, req):
        market = str(req.get("market", "")).lower()
        stat = MARKET_TO_STAT.get(market)
        if stat is None:
            return {"ok": False, "error": f"Market '{market}' is not supported by the model (points, rebounds, assists only)."}

        pid = self.find_player(req.get("player", ""))
        if pid is None:
            return {"ok": False, "error": f"Player '{req.get('player')}' was not found in player_dim."}
        home_id, away_id = self.find_team(req.get("home_team", "")), self.find_team(req.get("away_team", ""))
        if home_id is None or away_id is None:
            return {"ok": False, "error": "Could not match the event's teams to team_dim."}

        as_of = self.as_of(req.get("match_date"))
        own_rows = self.load_player_rows(pid, as_of)
        prior = own_rows[(own_rows.minutes > 0) & before(own_rows, as_of)].sort_values(["ts", "gameid"])
        if prior.empty:
            return {"ok": False, "error": "No recent games found for this player."}
        team_id = int(prior.iloc[-1].teamid)
        if team_id not in (home_id, away_id):
            return {"ok": False, "error": "The player's most recent team is not in this event (traded or inactive)."}
        opp_id = away_id if team_id == home_id else home_id

        pl, tl = self.load_context([home_id, away_id], as_of)
        feats, pos = self.build_features(pid, team_id, opp_id, team_id == home_id, as_of, pl, tl, own_rows)
        mu, _ = self.predict_row(feats, stat)

        result = {
            "ok": True, "player": req.get("player"), "stat": stat, "market": market,
            "predicted": round(mu, 2), "position": pos, "is_home": team_id == home_id,
            "games_in_window": int(len(prior)),
            "avg_l5": _num(feats.get(f"{stat}_avg_l5")), "avg_l10": _num(feats.get(f"{stat}_avg_l10")),
            "avg_season": _num(feats.get(f"{stat}_avg_season")), "minutes_avg_l10": _num(feats.get("minutes_avg_l10")),
        }
        line = req.get("line")
        if line is not None:
            p_over = prob_over(mu, float(line), self.alphas[stat])
            result.update({"line": float(line), "p_over": round(p_over, 4), "p_under": round(1 - p_over, 4),
                           "lean": "Over" if p_over >= 0.5 else "Under"})
            selection = str(req.get("selection", "")).strip().lower()
            if selection in ("over", "under"):
                result["selection"] = selection.capitalize()
                result["p_pick"] = result["p_over"] if selection == "over" else result["p_under"]
        return result

    @staticmethod
    def as_of(match_date):
        if not match_date:
            return pd.Timestamp(datetime.now(EASTERN).replace(tzinfo=None))
        dt = pd.Timestamp(match_date)
        if dt.tzinfo is not None:
            dt = dt.tz_convert(EASTERN).tz_localize(None)
        return dt


def _num(value):
    return None if value is None or pd.isna(value) else round(float(value), 2)


def main():
    requests = json.load(sys.stdin)
    predictor = Predictor()
    out = []
    for req in requests:
        try:
            out.append(predictor.predict_request(req))
        except Exception as exc:  # surface per-request failures without dropping the batch
            log(f"Prediction failed for {req}: {exc!r}")
            out.append({"ok": False, "error": str(exc)})
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()

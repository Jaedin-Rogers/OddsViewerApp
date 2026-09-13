import os
import time
import argparse
import logging
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from nba_api.stats.endpoints import leaguegamelog
from config import get_setting

load_dotenv()

# ==========================================
# Logging Configuration
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("PlayerLogSync")


def log_sample_results(label: str, result_proxy, limit: int = 5):
    rows = result_proxy.fetchall()
    total_count = len(rows)
    logger.info(f"{label} - Total Processed Rows: {total_count}")
    if total_count > 0:
        logger.info(f"--- First {min(limit, total_count)} Sample Rows ---")
        for i, row in enumerate(rows[:limit]):
            logger.info(f"  [{i + 1}] {dict(row._mapping)}")
    else:
        logger.info("  (No rows affected/returned)")


# ==========================================
# Helper: Format Season String
# ==========================================
def format_nba_season(season_input: str = None, date_str: str = None) -> str:
    """
    Normalizes season input to NBA format 'YYYY-YY'.
    Accepts '2024', '2024-25', '2024-2025', or infers from a date (YYYY-MM-DD).
    """
    if season_input:
        s = str(season_input).strip()
        if len(s) == 4 and s.isdigit():
            # e.g. "2024" -> "2024-25"
            start_yr = int(s)
            end_yr_short = str(start_yr + 1)[-2:]
            return f"{start_yr}-{end_yr_short}"
        if len(s) == 9 and "-" in s:
            # e.g. "2024-2025" -> "2024-25"
            parts = s.split("-")
            return f"{parts[0]}-{parts[1][-2:]}"
        return s

    if date_str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        # NBA regular season starts in October
        start_year = dt.year if dt.month >= 10 else dt.year - 1
        return f"{start_year}-{str(start_year + 1)[-2:]}"

    current_dt = datetime.now()
    start_year = current_dt.year if current_dt.month >= 10 else current_dt.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


# ==========================================
# Fetch Box Score Stats from NBA API (Free)
# ==========================================
def fetch_nba_api_player_logs(
    season: str = None,
    season_type: str = "Regular Season",
    start_date: str = None,
    end_date: str = None
) -> pd.DataFrame:
    """
    Fetches player box score logs using nba_api LeagueGameLog.
    """
    formatted_season = format_nba_season(season, start_date or end_date)
    logger.info(f"Fetching player logs via nba_api (Season: {formatted_season}, Type: {season_type}, Range: {start_date} to {end_date})...")

    kwargs = {
        "season": formatted_season,
        "season_type_all_star": season_type,
        "player_or_team_abbreviation": "P"  # 'P' for player logs
    }
    if start_date:
        kwargs["date_from_nullable"] = datetime.strptime(start_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    if end_date:
        kwargs["date_to_nullable"] = datetime.strptime(end_date, "%Y-%m-%d").strftime("%m/%d/%Y")

    try:
        log_endpoint = leaguegamelog.LeagueGameLog(**kwargs)
        df = log_endpoint.get_data_frames()[0]
        logger.info(f"Successfully retrieved {len(df)} player game log records from NBA.com.")
        return df
    except Exception as e:
        logger.error(f"Error querying NBA API: {e}")
        return pd.DataFrame()


# ==========================================
# Transform & Upsert Player Logs
# ==========================================
def sync_player_logs(
    engine: Engine,
    season: str = None,
    season_type: str = "Regular Season",
    start_date: str = None,
    end_date: str = None
):
    df_raw = fetch_nba_api_player_logs(
        season=season,
        season_type=season_type,
        start_date=start_date,
        end_date=end_date
    )

    if df_raw.empty:
        logger.warning("No player logs found for the given criteria.")
        return

    # Parse names from 'PLAYER_NAME'
    def split_name(name_str):
        parts = str(name_str).strip().split()
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    names = df_raw["PLAYER_NAME"].apply(split_name)
    df_transformed = pd.DataFrame()
    df_transformed["firstName"] = [n[0] for n in names]
    df_transformed["lastName"] = [n[1] for n in names]
    df_transformed["nba_player_id"] = df_raw["PLAYER_ID"]
    df_transformed["nba_game_id"] = df_raw["GAME_ID"]
    df_transformed["gameDate"] = pd.to_datetime(df_raw["GAME_DATE"]).dt.strftime("%Y-%m-%d")
    df_transformed["teamAbbrev"] = df_raw["TEAM_ABBREVIATION"]
    df_transformed["matchup"] = df_raw["MATCHUP"]
    df_transformed["win"] = df_raw["WL"].apply(lambda x: 1.0 if x == "W" else (0.0 if x == "L" else None))
    df_transformed["home"] = df_raw["MATCHUP"].apply(lambda x: 1.0 if "vs." in str(x) else 0.0)

    # Stats mappings
    df_transformed["numMinutes"] = df_raw["MIN"].astype(str)
    df_transformed["points"] = pd.to_numeric(df_raw["PTS"], errors="coerce")
    df_transformed["assists"] = pd.to_numeric(df_raw["AST"], errors="coerce")
    df_transformed["steals"] = pd.to_numeric(df_raw["STL"], errors="coerce")
    df_transformed["turnovers"] = pd.to_numeric(df_raw["TOV"], errors="coerce")
    df_transformed["blocks"] = pd.to_numeric(df_raw["BLK"], errors="coerce")
    df_transformed["plusminus"] = pd.to_numeric(df_raw["PLUS_MINUS"], errors="coerce")
    df_transformed["reboundsTotal"] = pd.to_numeric(df_raw["REB"], errors="coerce")
    df_transformed["reboundsOffensive"] = pd.to_numeric(df_raw["OREB"], errors="coerce")
    df_transformed["reboundsDefensive"] = pd.to_numeric(df_raw["DREB"], errors="coerce")
    df_transformed["fieldGoalsMade"] = pd.to_numeric(df_raw["FGM"], errors="coerce")
    df_transformed["fieldGoalsAttempted"] = pd.to_numeric(df_raw["FGA"], errors="coerce")
    df_transformed["fieldGoalsPercentage"] = pd.to_numeric(df_raw["FG_PCT"], errors="coerce")
    df_transformed["threePointersMade"] = pd.to_numeric(df_raw["FG3M"], errors="coerce")
    df_transformed["threePointersAttempted"] = pd.to_numeric(df_raw["FG3A"], errors="coerce")
    df_transformed["threePointersPercentage"] = pd.to_numeric(df_raw["FG3_PCT"], errors="coerce")
    df_transformed["freeThrowsMade"] = pd.to_numeric(df_raw["FTM"], errors="coerce")
    df_transformed["freeThrowsAttempted"] = pd.to_numeric(df_raw["FTA"], errors="coerce")
    df_transformed["freeThrowsPercentage"] = pd.to_numeric(df_raw["FT_PCT"], errors="coerce")

    # Extract opponent abbreviation (e.g. 'LAL vs. GSW' -> 'GSW', 'BOS @ MIA' -> 'MIA')
    def extract_opp_abbrev(matchup_str):
        parts = str(matchup_str).replace("@", "vs.").split("vs.")
        if len(parts) == 2:
            return parts[1].strip()
        return None

    df_transformed["oppAbbrev"] = df_raw["MATCHUP"].apply(extract_opp_abbrev)

    with engine.begin() as conn:
        df_transformed.to_sql("_stg_player_log", conn, schema="play", if_exists="replace", index=False)

        # 1. Enrich staging table with database IDs (playerindex, teamId, gameId)
        conn.execute(text("""
            ALTER TABLE play._stg_player_log 
            ADD COLUMN IF NOT EXISTS playerindex double precision,
            ADD COLUMN IF NOT EXISTS "db_gameId" bigint,
            ADD COLUMN IF NOT EXISTS "playerteamId" bigint,
            ADD COLUMN IF NOT EXISTS "playerteamCity" text,
            ADD COLUMN IF NOT EXISTS "playerteamName" text,
            ADD COLUMN IF NOT EXISTS "opponentteamId" bigint,
            ADD COLUMN IF NOT EXISTS "opponentteamCity" text,
            ADD COLUMN IF NOT EXISTS "opponentteamName" text,
            ADD COLUMN IF NOT EXISTS "gameDateTimeEst" text,
            ADD COLUMN IF NOT EXISTS "gameType" text;
        """))

        # Match playerindex by name
        conn.execute(text("""
            UPDATE play._stg_player_log stg
            SET playerindex = p.playerindex
            FROM play.player_dim p
            WHERE LOWER(TRIM(p."firstName")) = LOWER(TRIM(stg."firstName"))
              AND LOWER(TRIM(p."lastName")) = LOWER(TRIM(stg."lastName"));
        """))

        # Match team info by abbreviation
        conn.execute(text("""
            UPDATE play._stg_player_log stg
            SET "playerteamId" = t."teamId",
                "playerteamCity" = t."teamCity",
                "playerteamName" = t."teamName"
            FROM team.team_dim t
            WHERE LOWER(TRIM(t."teamAbbrev")) = LOWER(TRIM(stg."teamAbbrev"));

            UPDATE play._stg_player_log stg
            SET "opponentteamId" = t."teamId",
                "opponentteamCity" = t."teamCity",
                "opponentteamName" = t."teamName"
            FROM team.team_dim t
            WHERE LOWER(TRIM(t."teamAbbrev")) = LOWER(TRIM(stg."oppAbbrev"));
        """))

        # Match gameId from game_dim using date and teams
        conn.execute(text("""
            UPDATE play._stg_player_log stg
            SET "db_gameId" = matched.selected_game_id,
                "gameDateTimeEst" = matched.selected_datetime,
                "gameType" = matched.selected_gametype
            FROM (
                SELECT DISTINCT ON (stg_inner.nba_player_id, stg_inner.nba_game_id)
                    stg_inner.nba_player_id,
                    stg_inner.nba_game_id,
                    g."gameId" AS selected_game_id,
                    g."gameDateTimeEst" AS selected_datetime,
                    g."gameType" AS selected_gametype
                FROM play._stg_player_log stg_inner
                JOIN game.game_dim g
                  ON g."gameDate"::date = stg_inner."gameDate"::date
                 AND (
                     (g."hometeamId" = stg_inner."playerteamId" AND g."awayteamId" = stg_inner."opponentteamId")
                     OR
                     (g."awayteamId" = stg_inner."playerteamId" AND g."hometeamId" = stg_inner."opponentteamId")
                 )
                ORDER BY stg_inner.nba_player_id, stg_inner.nba_game_id, LENGTH(g."gameDateTimeEst") DESC
            ) matched
            WHERE stg.nba_player_id = matched.nba_player_id
              AND stg.nba_game_id = matched.nba_game_id;
        """))

        # 2. Log & Remove records that couldn't match player_dim or game_dim
        res_skipped_players = conn.execute(text("""
            DELETE FROM play._stg_player_log
            WHERE playerindex IS NULL
            RETURNING "firstName", "lastName", "teamAbbrev", "gameDate";
        """))
        log_sample_results("Skipped Player Logs (Unmatched player_dim)", res_skipped_players)

        res_skipped_games = conn.execute(text("""
            DELETE FROM play._stg_player_log
            WHERE "db_gameId" IS NULL
            RETURNING "firstName", "lastName", "teamAbbrev", "oppAbbrev", "gameDate";
        """))
        log_sample_results("Skipped Player Logs (No Matching gameId in game_dim)", res_skipped_games)

        # 3. Clear existing logs for matching player and date
        conn.execute(text("""
            DELETE FROM play.player_log pl
            USING play._stg_player_log stg, game.game_dim g
            WHERE pl.playerindex = stg.playerindex
              AND pl."gameId" = g."gameId"
              AND g."gameDate"::date = stg."gameDate"::date;
        """))

        # 4. Insert transformed player logs
        insert_res = conn.execute(text("""
            INSERT INTO play.player_log (
                "firstName", "lastName", playerid, playerindex,
                "gameId", "gameDateTimeEst", "gameType",
                win, home,
                playerteamid, "playerteamCity", "playerteamName",
                opponentteamid, "opponentteamCity", "opponentteamName",
                "numMinutes", points, assists, steals, turnovers, blocks, "plusminus",
                "reboundsTotal", "reboundsOffensive", "reboundsDefensive",
                "fieldGoalsMade", "fieldGoalsAttempted", "fieldGoalsPercentage",
                "threePointersMade", "threePointersAttempted", "threePointersPercentage",
                "freeThrowsMade", "freeThrowsAttempted", "freeThrowsPercentage"
            )
            SELECT DISTINCT ON (stg.playerindex, stg."db_gameId")
                stg."firstName",
                stg."lastName",
                stg.nba_player_id,
                stg.playerindex,
                stg."db_gameId",
                stg."gameDateTimeEst",
                stg."gameType",
                stg.win,
                stg.home,
                stg."playerteamId",
                stg."playerteamCity",
                stg."playerteamName",
                stg."opponentteamId",
                stg."opponentteamCity",
                stg."opponentteamName",
                stg."numMinutes",
                stg.points,
                stg.assists,
                stg.steals,
                stg.turnovers,
                stg.blocks,
                stg."plusminus",
                stg."reboundsTotal",
                stg."reboundsOffensive",
                stg."reboundsDefensive",
                stg."fieldGoalsMade",
                stg."fieldGoalsAttempted",
                stg."fieldGoalsPercentage",
                stg."threePointersMade",
                stg."threePointersAttempted",
                stg."threePointersPercentage",
                stg."freeThrowsMade",
                stg."freeThrowsAttempted",
                stg."freeThrowsPercentage"
            FROM play._stg_player_log stg
            RETURNING "gameId", "firstName", "lastName", points, assists, steals, turnovers, blocks, "plusminus", "reboundsTotal", "numMinutes";
        """))

        log_sample_results("play.player_log INSERTS / UPDATES", insert_res)
        conn.execute(text("DROP TABLE IF EXISTS play._stg_player_log;"))

    logger.info("Player log sync completed successfully.")


# ==========================================
# CLI Entrypoint
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Sync player game logs using free NBA stats API into play.player_log.")
    parser.add_argument("--season", type=str, default=None, help="Season format (e.g. 2024, 2024-25, 2024-2025). Auto-inferred if omitted.")
    parser.add_argument("--season_type", type=str, default="Regular Season", choices=["Regular Season", "Playoffs", "Pre Season"])
    parser.add_argument("--start_date", type=str, help="Filter from date (YYYY-MM-DD), e.g. 2026-01-01")
    parser.add_argument("--end_date", type=str, help="Filter to date (YYYY-MM-DD), e.g. 2026-03-01")

    args = parser.parse_args()

    db_user = get_setting("DB_USER") or get_setting("pg_user")
    db_pass = get_setting("DB_PASSWORD") or get_setting("pg_password")
    db_host = get_setting("DB_HOST", "localhost") or get_setting("pg_host", "localhost")
    db_port = get_setting("DB_PORT", "5432") or get_setting("pg_port", "5432")
    db_name = get_setting("DB_NAME", "nba") or get_setting("pg_database", "NBA")

    engine = create_engine(f"postgresql+psycopg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}")

    sync_player_logs(
        engine=engine,
        season=args.season,
        season_type=args.season_type,
        start_date=args.start_date,
        end_date=args.end_date
    )


if __name__ == "__main__":
    main()
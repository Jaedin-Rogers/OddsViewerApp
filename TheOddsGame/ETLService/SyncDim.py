import os
import time
import pandas as pd
import dotenv
import sqlalchemy
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from balldontlie import BalldontlieAPI
import argparse
import logging
from config import get_setting

load_dotenv()

# ==========================================
# Logging Configuration
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("SyncDim")


def log_sample_results(title: str, cursor_result, limit: int = 20):
    """Fetches result records and logs a DataFrame preview of top N rows."""
    try:
        rows = cursor_result.fetchall()
        if not rows:
            logger.info(f"[{title}] 0 rows modified (no metadata differences found).")
            return

        cols = list(cursor_result.keys())
        df = pd.DataFrame(rows, columns=cols)
        preview = df.head(limit)

        logger.info(
            f"\n{'='*20} [{title}] ({len(df)} total rows updated, showing top {min(len(df), limit)}) {'='*20}\n"
            f"{preview.to_string(index=False)}\n"
            f"{'='*70}"
        )
    except Exception as e:
        logger.warning(f"Could not print preview for [{title}]: {e}")


# ==========================================
# Database Connection
# ==========================================
def get_engine() -> Engine:
    host = get_setting("DB_HOST")
    port = get_setting("DB_PORT", "5432")
    database = get_setting("DB_NAME")
    username = get_setting("DB_USER")
    password = get_setting("DB_PASSWORD")

    if not all([host, database, username, password]):
        raise ValueError("Missing required DB settings (DB_HOST, DB_NAME, DB_USER, DB_PASSWORD).")

    url = f"postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"
    return create_engine(url, pool_pre_ping=True, connect_args={"sslmode": "require"})


# ==========================================
# 1. Sync Teams Pipeline
# ==========================================
def sync_teams(api: BalldontlieAPI, engine: Engine, single: bool = False, team_name: str = None, team_city: str = None):
    logger.info("Fetching teams from BallDontLie...")
    teams_resp = api.nba.teams.list()
    teams = [t.model_dump() if hasattr(t, "model_dump") else t for t in teams_resp.data]
    df_teams = pd.json_normalize(teams)

    if single:
        # --- SINGLE MODE: INSERT OR UPDATE (UPSERT) ---
        if not team_name:
            raise ValueError("Parameter --team_name is required when --single 1 is used with SyncTeams.")

        mask = df_teams["name"].str.strip().str.lower() == team_name.strip().lower()
        if team_city:
            mask = mask & (df_teams["city"].str.strip().str.lower() == team_city.strip().lower())

        df_teams = df_teams[mask]
        if df_teams.empty:
            logger.warning(f"No team found on BallDontLie matching name='{team_name}'" + (f" and city='{team_city}'" if team_city else ""))
            return

        with engine.begin() as conn:
            df_teams.to_sql("_stg_teams", conn, schema="team", if_exists="replace", index=False)

            # 1. Update existing if matched by name
            res_update = conn.execute(text("""
                UPDATE team.team_dim t
                SET 
                    "teamId" = stg.id,
                    "teamCity" = stg.city,
                    "teamAbbrev" = stg.abbreviation
                FROM team._stg_teams stg
                WHERE LOWER(TRIM(t."teamName")) = LOWER(TRIM(stg.name))
                RETURNING t."teamId", t."teamCity", t."teamName", t."teamAbbrev";
            """))
            log_sample_results("Single Sync - team.team_dim UPDATE", res_update)

            # 2. Insert new team if does not exist
            res_insert = conn.execute(text("""
                INSERT INTO team.team_dim ("teamId", "teamCity", "teamName", "teamAbbrev")
                SELECT stg.id, stg.city, stg.name, stg.abbreviation
                FROM team._stg_teams stg
                WHERE LOWER(TRIM(stg.name)) NOT IN (
                    SELECT LOWER(TRIM("teamName")) FROM team.team_dim WHERE "teamName" IS NOT NULL
                )
                ON CONFLICT ("teamId") DO UPDATE 
                SET "teamCity" = EXCLUDED."teamCity",
                    "teamName" = EXCLUDED."teamName",
                    "teamAbbrev" = EXCLUDED."teamAbbrev"
                RETURNING "teamId", "teamCity", "teamName", "teamAbbrev";
            """))
            log_sample_results("Single Sync - team.team_dim INSERT", res_insert)
            conn.execute(text("DROP TABLE IF EXISTS team._stg_teams;"))

    else:
        # --- BATCH / ALL MODE: DB IS SOURCE OF TRUTH (UPDATE ONLY ON METADATA DIFF) ---
        with engine.begin() as conn:
            df_teams.to_sql("_stg_teams", conn, schema="team", if_exists="replace", index=False)

            # Only update existing DB teams where attributes differ
            res_batch = conn.execute(text("""
                UPDATE team.team_dim t
                SET 
                    "teamId" = stg.id,
                    "teamCity" = stg.city,
                    "teamAbbrev" = stg.abbreviation
                FROM team._stg_teams stg
                WHERE LOWER(TRIM(t."teamName")) = LOWER(TRIM(stg.name))
                  AND (
                      t."teamId" IS DISTINCT FROM stg.id OR
                      t."teamCity" IS DISTINCT FROM stg.city OR
                      t."teamAbbrev" IS DISTINCT FROM stg.abbreviation
                  )
                RETURNING t."teamId", t."teamCity", t."teamName", t."teamAbbrev";
            """))
            log_sample_results("Batch Sync - team.team_dim DIFF UPDATE", res_batch)
            conn.execute(text("DROP TABLE IF EXISTS team._stg_teams;"))

    logger.info("Team sync process completed.")


# ==========================================
# 2. Extract Players Helper
# ==========================================
def fetch_players_api(api: BalldontlieAPI, single: bool = False, first_name: str = None, last_name: str = None, max_players: int = 5000) -> pd.DataFrame:
    all_players = []
    cursor = None
    backoff = 5
    page_count = 0

    if single:
        if not first_name or not last_name:
            raise ValueError("Both --first_name and --last_name are required when --single 1 is used with SyncPlayers.")
        
        target_fn = first_name.strip().lower()
        target_ln = last_name.strip().lower()
        logger.info(f"Searching API directly for: '{first_name} {last_name}'...")

        try:
            resp = api.nba.players.list(search=last_name.strip(), per_page=100)
            page_records = [p.model_dump() if hasattr(p, "model_dump") else p for p in resp.data]
            matched = [
                p for p in page_records
                if str(p.get("first_name", "")).strip().lower() == target_fn
                and str(p.get("last_name", "")).strip().lower() == target_ln
            ]
            if matched:
                logger.info(f"Found match: {matched[0].get('first_name')} {matched[0].get('last_name')} (ID: {matched[0].get('id')})")
                return pd.json_normalize(matched)
            else:
                logger.warning(f"Player '{first_name} {last_name}' not found in search results.")
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"Error fetching single player: {e}")
            return pd.DataFrame()

    else:
        logger.info(f"Fetching players from BallDontLie (all pages up to {max_players})...")

    while True:
        try:
            resp = api.nba.players.list(cursor=cursor, per_page=100)

            # Model dump is used to convert API response objects to dictionaries for easier processing
            # This is the solution for unstructured API data response

            page_records = [p.model_dump() if hasattr(p, "model_dump") else p for p in resp.data]
            all_players.extend(page_records)
            page_count += 1
            
            logger.info(f"Fetched page {page_count} ({len(page_records)} players, total: {len(all_players)})")

            if len(all_players) >= max_players:
                logger.info(f"Reached player limit ({len(all_players)} >= {max_players}). Exiting extraction.")
                all_players = all_players[:max_players]
                break

            backoff = 30
            cursor = getattr(resp.meta, "next_cursor", None)
            if not cursor:
                break
            
            time.sleep(2.2)
        except Exception as e:
            if "Too Many Requests" in str(e) or "429" in str(e):
                logger.warning(f"Rate limit hit. Waiting {backoff} seconds before retrying...")
                time.sleep(backoff)
                backoff = min(backoff + 1, 60)
                continue
            logger.error(f"Stopped fetching players: {e}")
            break

    return pd.json_normalize(all_players)


# ==========================================
# 3. Sync Players Pipeline
# ==========================================
def sync_players(api: BalldontlieAPI, engine: Engine, single: bool = False, first_name: str = None, last_name: str = None):
    df_players = fetch_players_api(api, single=single, first_name=first_name, last_name=last_name)
    if df_players.empty:
        logger.warning("No players returned from API.")
        return

    # Guarantee expected columns exist in dataframe
    expected_cols = ["position", "team.id", "jersey_number", "country", "college", "draft_year", "to_year"]
    for col in expected_cols:
        if col not in df_players.columns:
            df_players[col] = None

    # Derive position flags
    df_players["guard"] = df_players["position"].fillna("").str.contains("G").astype(int)
    df_players["forward"] = df_players["position"].fillna("").str.contains("F").astype(int)
    df_players["center"] = df_players["position"].fillna("").str.contains("C").astype(int)
    df_players["draft_year"] = pd.to_numeric(df_players["draft_year"], errors="coerce")

    with engine.begin() as conn:
        df_players.to_sql("_stg_players", conn, schema="play", if_exists="replace", index=False)

        # Remove player rows with invalid/unknown team IDs not present in team.team_dim
        conn.execute(text("""
            DELETE FROM play._stg_players
            WHERE "team.id" IS NOT NULL 
              AND "team.id" NOT IN (SELECT "teamId" FROM team.team_dim);
        """))

        if single:
            # --- SINGLE MODE: INSERT OR UPDATE (UPSERT) ---
            # 1. Update existing player if exists
            res_update = conn.execute(text("""
                UPDATE play.player_dim p
                SET 
                    playerteamid = stg."team.id",
                    jersey = stg."jersey_number",
                    guard = stg.guard,
                    forward = stg.forward,
                    center = stg.center,
                    country = COALESCE(p.country, stg.country),
                    school = COALESCE(p.school, stg.college),
                    "nbaFlag" = 1
                FROM play._stg_players stg
                WHERE LOWER(TRIM(p."firstName")) = LOWER(TRIM(stg.first_name))
                  AND LOWER(TRIM(p."lastName")) = LOWER(TRIM(stg.last_name))
                RETURNING p.playerindex, p."firstName", p."lastName", p.playerteamid, p.jersey, p.guard, p.forward, p.center;
            """))
            log_sample_results("Single Sync - play.player_dim UPDATE", res_update)

            # 2. Insert new player if doesn't exist in player_dim
            res_insert = conn.execute(text("""
                INSERT INTO play.player_dim (
                    playerindex, "firstName", "lastName", playerteamid, jersey, 
                    guard, forward, center, country, school, "nbaFlag"
                )
                SELECT 
                    COALESCE((SELECT MAX(playerindex) FROM play.player_dim), 0) + ROW_NUMBER() OVER (),
                    stg.first_name, stg.last_name, stg."team.id", stg."jersey_number",
                    stg.guard, stg.forward, stg.center, stg.country, stg.college, 1
                FROM play._stg_players stg
                WHERE NOT EXISTS (
                    SELECT 1 FROM play.player_dim p 
                    WHERE LOWER(TRIM(p."firstName")) = LOWER(TRIM(stg.first_name))
                      AND LOWER(TRIM(p."lastName")) = LOWER(TRIM(stg.last_name))
                )
                RETURNING playerindex, "firstName", "lastName", playerteamid, jersey, guard, forward, center;
            """))
            log_sample_results("Single Sync - play.player_dim INSERT", res_insert)
            ### NOTE: NEW PLAYERS WILL NEED THEIR GAME LOGS REFRESHED AFTER INSERTION

        else:
            # --- BATCH / ALL MODE: DB IS SOURCE OF TRUTH (UPDATE ONLY ON METADATA DIFF) ---
            res_batch = conn.execute(text("""
                UPDATE play.player_dim p
                SET 
                    playerteamid = stg."team.id",
                    jersey = stg."jersey_number",
                    guard = stg.guard,
                    forward = stg.forward,
                    center = stg.center,
                    country = COALESCE(p.country, stg.country),
                    school = COALESCE(p.school, stg.college),
                    "nbaFlag" = 1
                FROM play._stg_players stg
                WHERE LOWER(TRIM(p."firstName")) = LOWER(TRIM(stg.first_name))
                  AND LOWER(TRIM(p."lastName")) = LOWER(TRIM(stg.last_name))
                  AND (
                      p.playerteamid IS DISTINCT FROM stg."team.id" OR
                      p.jersey IS DISTINCT FROM stg."jersey_number" OR
                      p.guard IS DISTINCT FROM stg.guard OR
                      p.forward IS DISTINCT FROM stg.forward OR
                      p.center IS DISTINCT FROM stg.center OR
                      (p.country IS NULL AND stg.country IS NOT NULL) OR
                      (p.school IS NULL AND stg.college IS NOT NULL) OR
                      p."nbaFlag" IS DISTINCT FROM 1
                  )
                RETURNING p.playerindex, p."firstName", p."lastName", p.playerteamid, p.jersey, p.guard, p.forward, p.center;
            """))
            log_sample_results("Batch Sync - play.player_dim DIFF UPDATE", res_batch)

            # 2. Insert new players if draft_year > 2020 and toyear is NULL (active recent players)
            res_insert = conn.execute(text("""
                INSERT INTO play.player_dim (
                    playerindex, "firstName", "lastName", playerteamid, jersey, 
                    guard, forward, center, country, school, "nbaFlag"
                )
                SELECT 
                    COALESCE((SELECT MAX(playerindex) FROM play.player_dim), 0) + ROW_NUMBER() OVER (),
                    stg.first_name, stg.last_name, stg."team.id", stg."jersey_number",
                    stg.guard, stg.forward, stg.center, stg.country, stg.college, 1
                FROM (
                    SELECT DISTINCT ON (LOWER(TRIM(first_name)), LOWER(TRIM(last_name)))
                        first_name, last_name, "team.id", "jersey_number",
                        guard, forward, center, country, college, draft_year, to_year
                    FROM play._stg_players
                    WHERE draft_year > 2008
                      AND to_year IS NULL
                ) stg
                WHERE NOT EXISTS (
                    SELECT 1 FROM play.player_dim p 
                    WHERE LOWER(TRIM(p."firstName")) = LOWER(TRIM(stg.first_name))
                      AND LOWER(TRIM(p."lastName")) = LOWER(TRIM(stg.last_name))
                )
                RETURNING playerindex, "firstName", "lastName", playerteamid, jersey, guard, forward, center;
            """))
            log_sample_results("Batch Sync - play.player_dim INSERT (Draft > 2008 & toyear IS NULL)", res_insert)

        conn.execute(text("DROP TABLE IF EXISTS play._stg_players;"))

    logger.info("Player sync process completed.")


# ==========================================
# Entrypoint
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Sync NBA Dimension Tables")
    parser.add_argument(
        "--process",
        choices=["SyncTeams", "SyncPlayers", "all"],
        default="all",
        help="Pipeline process to run: 'SyncTeams', 'SyncPlayers', or 'all' (default: 'all')",
    )
    parser.add_argument(
        "--single",
        type=int,
        choices=[0, 1],
        default=0,
        help="1 to sync/insert a single player or team, 0 for batch diff update against DB source of truth (default: 0)",
    )
    parser.add_argument("--first_name", type=str, default=None, help="First name of single player")
    parser.add_argument("--last_name", type=str, default=None, help="Last name of single player")
    parser.add_argument("--team_name", type=str, default=None, help="Team name (e.g., 'Thunder')")
    parser.add_argument("--team_city", type=str, default=None, help="Optional team city (e.g., 'Oklahoma City')")

    args = parser.parse_args()

    is_single = bool(args.single)

    if is_single and args.process == "all":
        parser.error("--single 1 requires specifying either --process SyncPlayers or --process SyncTeams.")

    api_key = get_setting("BALLDONTLIE_API_KEY")
    if not api_key:
        raise ValueError("Missing required BALLDONTLIE_API_KEY setting.")
    api = BalldontlieAPI(api_key=api_key)
    engine = get_engine()

    logger.info(f"--- Starting Process: {args.process} (single={is_single}) ---")

    if args.process in ("SyncTeams", "all"):
        sync_teams(api, engine, single=is_single, team_name=args.team_name, team_city=args.team_city)

    if args.process in ("SyncPlayers", "all"):
        sync_players(api, engine, single=is_single, first_name=args.first_name, last_name=args.last_name)

    logger.info("--- ETL Completed Successfully ---")


if __name__ == "__main__":
    main()
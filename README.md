# The Odds Game


A Blazor Server web app for browsing NBA odds, player props, and NBA stats/game logs. Live odds come from [The Odds API](https://the-odds-api.com/); historical NBA stats are synced from `nba_api`/BALLDONTLIE into a PostgreSQL database via Python ETL scripts.


## Pages


| Page | Route | Description |
|---|---|---|
| Odds Dashboard | `/` or `/odds` | NBA moneyline/totals/player-props odds, filterable by team, player, bookmaker, and market. Sport is hardcoded to NBA (`basketball_nba`). |
| Player Props | `/player-props` | NBA-only event/market picker for player prop odds (points, rebounds, assists, etc.), with bet selection, ML-ready JSON export, and a **Predict** button that scores selected bets with the trained stat models. |
| Game Results & Box Scores | `/stat` | Most recent games first (date, home, away, score). Search by team, filter by date, and click a game for a box score dialog with a home/away toggle and team totals. |
| Player History | `/player-history`, `/player-history/{playerId}` | Search players by name or list a team's roster, then view a player's games 10 per page with the full box score line and the team result. Linked from every player name in a box score. |


## Features


- Browse NBA odds and player props (moneyline, totals, and player prop markets), configurable via `OddsApi` settings
- Filter by team, player, bookmaker, and odds type (market)
- Dynamic table columns — columns with all-null values (e.g. Over/Under on H2H markets) are hidden automatically
- Match dates displayed in a configurable timezone with abbreviation (e.g. `2026-07-12 19:00 CDT`)
- American odds format (configurable)
- Pandas-ready / ML-ready JSON export preview for selected bets
- Team & player game log browser backed by a PostgreSQL database
- Box scores: click any game to see every player's line (MIN, PTS, REB, AST, STL, BLK, TO, FG, 3PT, FT, +/-) with a home/away toggle and a totals row
- Player history: paged game logs with team results, plus a "starting at" date filter (shows games on or before the chosen date, newest first) on both the games list and a player's history
- Model predictions for points, rebounds and assists props: predicted stat, model probability for the selected Over/Under, and edge versus the book's implied probability
- MudBlazor UI with light/dark theme support


## Tech Stack


| Layer | Technology |
|---|---|
| Framework | .NET 10, Blazor Server (interactive server render mode) |
| UI Components | MudBlazor |
| Live Odds Data | The Odds API v4 |
| Stats Database | PostgreSQL (queried via Dapper/Npgsql) |
| ETL / Data Pipelines | Python (`pandas`, `sqlalchemy`, `psycopg`, `nba_api`, `balldontlie`) |
| ML | scikit-learn gradient boosting (Poisson loss) + negative-binomial probabilities, served by a Python script the web app calls |
| Styling | Bootstrap + MudBlazor theming |


## Prerequisites


- [.NET 10 SDK](https://dotnet.microsoft.com/download)
- A free or paid API key from [The Odds API](https://the-odds-api.com/)
- A PostgreSQL database (e.g. Azure Database for PostgreSQL) populated by the ETL scripts, for the Stats & Game Logs page
- Python 3.11+ if you plan to run the ETL scripts in `TheOddsGame/ETLService/` locally


## Getting Started


### 1. Clone the repo


```bash
git clone https://github.com/<your-username>/OddsViewerApp.git
cd OddsViewerApp
```


### 2. Configure your API key


> **Security:** Never commit your real API key to source control. Use one of the options below.


**Option A — User secrets (recommended for development)**


```bash
cd TheOddsGame
dotnet user-secrets init
dotnet user-secrets set "OddsApi:ApiKey" "YOUR_API_KEY"
```


**Option B — Environment variable**


```bash
# Windows PowerShell
$env:OddsApi__ApiKey = "YOUR_API_KEY"
```


**Option C — appsettings.json (not recommended for shared repos)**


Edit TheOddsGame/appsettings.json` and replace the `ApiKey` value. **Do not commit this file with a real key.**


### 3. Run


```bash
cd TheOddsGame
dotnet run
```


Navigate to `https://localhost:5001` (or the port shown in the terminal).


## Configuration


### The Odds API

All options live under the `OddsApi` section in `appsettings.json`:


| Key | Default | Description |
|---|---|---|
| `ApiKey` | _(empty)_ | Your The Odds API key |
| `BaseUrl` | `https://api.the-odds-api.com/v4` | API base URL |
| `Regions` | `us` | Bookmaker regions (`us`, `uk`, `eu`, `au`) |
| `Markets` | `h2h,totals,player_props` | Comma-separated market keys to fetch |
| `OddsFormat` | `american` | `american` or `decimal` |
| `DefaultBookmakers` | `DraftKings, FanDuel, BetMGM` | Default bookmaker display filter |

> The Odds Dashboard and Player Props pages are hardcoded to the NBA sport key (`basketball_nba`); no sport picker is shown in the UI.

### NBA Stats Database

The Game Results, Player History and Player Props pages read from a PostgreSQL database via `BoxScoreService`, `PlayerHistoryService` and `NBADBService`. Connection settings are configured through `projectsettings.json` (see `projectsettings.example.json` for the template — copy it to `projectsettings.json` and fill in real values, which is git-ignored) or environment variables:


| Key | Description |
|---|---|
| `DB_HOST` | PostgreSQL server host |
| `DB_PORT` | PostgreSQL server port (default `5432`) |
| `DB_NAME` | Database name |
| `DB_USER` | Database username |
| `DB_PASSWORD` | Database password |

These same values are also used by the Python ETL scripts and can alternatively be provided via a `.env` file or repository secrets for CI.

### Box score and player history data

- Games come from `game.game_dim` (scores, teams) and player lines from `play.player_log`. Game times are normalized with `ml.parse_game_ts`, the same function the `ml` views use, because `gameDateTimeEst` is stored as text in two formats (`9/1/20 17:30` and `2026-04-24 20:00:00`).
- The services deliberately avoid `ml.player_game_base`: its window function forces a scan of all ~790k player rows per query (15-20 s per box score vs ~0.3 s direct).
- Upcoming games exist in `game_dim` with 0-0 scores; the games list hides them.
- Player names are stored ASCII-folded (e.g. `Luka Doncic`); `update_playerlog.py` strips diacritics from NBA API names so they match `player_dim`.
- Team rosters in Player History come from `player_dim.playerteamid`, which is only as fresh as the last `SyncDim.py` run.

### Machine learning predictions

The Predict button on the Player Props page sends the selected bets to `TheOddsGame/MLService/predict_player.py` (run as a process; JSON in via stdin, JSON out via stdout).

| File | Purpose |
|---|---|
| `MLService/train_player.py` | Builds features from the `ml` views, trains one Poisson gradient-boosting model per stat (points, assists, rebounds), and saves `models/player_stat_models.joblib`. |
| `MLService/predict_player.py` | Rebuilds the pre-game features for an upcoming game from the raw logs, predicts the mean, and converts it to P(over)/P(under) for the line with a negative-binomial distribution. |
| `MLService/validate_predict.py` | Checks the serving features against the SQL views on played games and compares model error to L10-average. |

Settings live under `Ml` in `appsettings.json`:

| Key | Default | Description |
|---|---|---|
| `PythonPath` | `python` | Interpreter with the packages in `requirements.txt` installed. The model file was saved with scikit-learn 1.9.1, so use the same or a newer version. |
| `ScriptPath` | `MLService/predict_player.py` | Relative paths resolve from the web project folder. |
| `TimeoutSeconds` | `180` | Kill the script if a prediction batch takes longer. |

Limitations: only `player_points`, `player_rebounds`, `player_assists` (and `_alternate`) markets are supported; there is no injury or lineup data; each click starts a new Python process (a few seconds). On a small validation sample the model did not yet beat a plain last-10-games average, so treat the output as experimental and check calibration before relying on it.


## Data Pipelines

The repository includes GitHub Actions workflows for loading NBA reference and statistics data into the configured database. Each workflow runs on Ubuntu with Python 3.11 and expects these repository secrets:

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `BALLDONTLIE_API_KEY` (used by player and game-log syncs)

### Scheduled and manual workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `Sync All Players Dimension` | Daily at 08:00 UTC, or manually | Runs `SyncDim.py --process SyncPlayers --single 0` to synchronize the player dimension. |
| `Sync Teams Dimension` | Fridays at 18:00 UTC, or manually | Synchronizes the team dimension. Manual runs can select a batch update or one team by name and city. |
| `Sync Single Player Dimension` | Manually | Runs a single-player dimension sync using required `first_name` and `last_name` inputs. |
| `Daily NBA Sync (Game Logs)` | Daily at 10:00 UTC, or manually | Synchronizes game logs (`update_gamelog.py`) for a selected season and date range (defaults to the previous 3 days through today). |
| `Sync Team Logs` | Daily at 6:00 AM CT, or manually | Synchronizes team logs (`update_teamlog.py`) for a selected season and date range. |
| `Sync Player Logs` | Daily at 5:00 AM CT, or manually | Synchronizes player logs (`update_playerlog.py`) for a selected season and date range. |

The dimension workflows install dependencies from the repository-level `requirements.txt`. The NBA log workflow installs its dependencies directly in the workflow, including `pandas`, `sqlalchemy`, `psycopg`, `python-dotenv`, `balldontlie`, and `nba_api`.

Workflow files are in `.github/workflows/`, and the Python ETL scripts are in `TheOddsGame/ETLService/`.


### Display timezone


The match date column timezone is set in `TheOddsGame/Components/Pages/Odds.razor`:


```csharp
private const string DisplayTimeZoneId = "Eastern Standard Time";
```


Replace with any Windows timezone ID (e.g. `"Central Standard Time"`, `"UTC"`, `"Pacific Standard Time"`).


## Project Structure


```
OddsViewerApp/
└── TheOddsGame/
    ├── Components/
    │   ├── Pages/
    │   │   ├── Odds.razor              # Main odds viewer page
    │   │   ├── PlayerProps.razor       # Prop picker + model predictions
    │   │   ├── Stat.razor              # Game results list
    │   │   ├── BoxScoreDialogue.razor  # Box score dialog (team toggle, totals)
    │   │   └── PlayerHistory.razor     # Player search + paged game log
    │   └── Layout/                     # Shell layout, nav, reconnect modal
    ├── Models/
    │   ├── OddsModels.cs               # OddsRow, SportOption, EventOption
    │   ├── BoxScoreModels.cs           # GameSummary, GameBoxScore
    │   ├── PlayerBoxScore.cs
    │   ├── PlayerHistoryModels.cs      # PlayerListItem, PlayerGameLog
    │   └── PlayerPrediction.cs
    ├── Services/
    │   ├── OddsApiService.cs           # API client + response flattening
    │   ├── OddsApiOptions.cs           # Strongly-typed config
    │   ├── BoxScoreService.cs          # Recent games + box scores
    │   ├── PlayerHistoryService.cs     # Player search + game logs
    │   ├── PlayerPredictionService.cs  # Runs predict_player.py
    │   └── MlOptions.cs
    ├── MLService/                      # Training, serving, validation, model file
    ├── ETLService/                     # Python sync scripts
    ├── appsettings.json
    └── Program.cs
```


## Security Notes


- Keep your API key out of source control. Use [.NET User Secrets](https://learn.microsoft.com/aspnet/core/security/app-secrets) or environment variables.
- The `appsettings.json` in this repo should only contain placeholder/empty values for `ApiKey`.


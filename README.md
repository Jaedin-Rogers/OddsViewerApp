# OddsViewerApp


A Blazor Server web app for browsing NBA odds, player props, and NBA stats/game logs. Live odds come from [The Odds API](https://the-odds-api.com/); historical NBA stats are synced from `nba_api`/BALLDONTLIE into a PostgreSQL database via Python ETL scripts.


## Pages


| Page | Route | Description |
|---|---|---|
| Odds Dashboard | `/` or `/odds` | NBA moneyline/totals/player-props odds, filterable by team, player, bookmaker, and market. Sport is hardcoded to NBA (`basketball_nba`). |
| Player Props | `/player-props` | NBA-only event/market picker for player prop odds (points, rebounds, assists, etc.), with bet selection and ML-ready JSON export. |
| Stats & Game Logs | `/stat` | Browse team game logs and player box scores loaded from the Postgres NBA database, with team/season/player filtering and row pinning. |


## Features


- Browse NBA odds and player props (moneyline, totals, and player prop markets), configurable via `OddsApi` settings
- Filter by team, player, bookmaker, and odds type (market)
- Dynamic table columns — columns with all-null values (e.g. Over/Under on H2H markets) are hidden automatically
- Match dates displayed in a configurable timezone with abbreviation (e.g. `2026-07-12 19:00 CDT`)
- American odds format (configurable)
- Pandas-ready / ML-ready JSON export preview for selected bets
- Team & player game log browser backed by a PostgreSQL database, with pinning and client-side filtering that updates in real time
- MudBlazor UI with light/dark theme support


## Tech Stack


| Layer | Technology |
|---|---|
| Framework | .NET 10, Blazor Server (interactive server render mode) |
| UI Components | MudBlazor |
| Live Odds Data | The Odds API v4 |
| Stats Database | PostgreSQL (queried via Dapper/Npgsql) |
| ETL / Data Pipelines | Python (`pandas`, `sqlalchemy`, `psycopg`, `nba_api`, `balldontlie`) |
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

The Stats & Game Logs page reads from a PostgreSQL database via `NBADBService`. Connection settings are configured through `projectsettings.json` (see `projectsettings.example.json` for the template — copy it to `projectsettings.json` and fill in real values, which is git-ignored) or environment variables:


| Key | Description |
|---|---|
| `DB_HOST` | PostgreSQL server host |
| `DB_PORT` | PostgreSQL server port (default `5432`) |
| `DB_NAME` | Database name |
| `DB_USER` | Database username |
| `DB_PASSWORD` | Database password |

These same values are also used by the Python ETL scripts and can alternatively be provided via a `.env` file or repository secrets for CI.


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
| `Sync Team Logs` | Manually | Synchronizes team logs (`update_teamlog.py`) for a selected season and date range. |
| `Sync Player Logs` | Manually | Synchronizes player logs (`update_playerlog.py`) for a selected season and date range. |

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
    │   │   └── Odds.razor          # Main odds viewer page
    │   └── Layout/                 # Shell layout, nav, reconnect modal
    ├── Models/
    │   └── OddsModels.cs           # OddsRow, SportOption, EventOption
    ├── Services/
    │   ├── OddsApiService.cs       # API client + response flattening
    │   └── OddsApiOptions.cs       # Strongly-typed config
    ├── appsettings.json
    └── Program.cs
```


## Security Notes


- Keep your API key out of source control. Use [.NET User Secrets](https://learn.microsoft.com/aspnet/core/security/app-secrets) or environment variables.
- The `appsettings.json` in this repo should only contain placeholder/empty values for `ApiKey`.


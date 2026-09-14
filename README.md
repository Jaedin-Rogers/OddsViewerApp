# OddsViewerApp


A Blazor Server web app for browsing live sports odds powered by the [The Odds API](https://the-odds-api.com/).


## The Odds Game Features


- Browse odds across any sport supported by The Odds API
- Filter by team, player, and odds type (market)
- Markets: moneyline (H2H) and totals (Over/Under), configurable
- Dynamic table columns — columns with all-null values (e.g. Over/Under on H2H markets) are hidden automatically
- Match dates displayed in a configurable timezone with abbreviation (e.g. `2026-07-12 19:00 EDT`)
- American odds format (configurable)
- Pandas-ready JSON export preview (for future ML predictions)
- MudBlazor UI with light/dark theme support


## Tech Stack


| Layer | Technology |
|---|---|
| Framework | .NET 10, Blazor Server |
| UI Components | MudBlazor |
| Odds Data | The Odds API v4 |
| Styling | Bootstrap + MudBlazor theming |


## Prerequisites


- [.NET 10 SDK](https://dotnet.microsoft.com/download)
- A free or paid API key from [The Odds API](https://the-odds-api.com/)


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


All options live under the `OddsApi` section in `appsettings.json`:


| Key | Default | Description |
|---|---|---|
| `ApiKey` | _(empty)_ | Your The Odds API key |
| `BaseUrl` | `https://api.the-odds-api.com/v4` | API base URL |
| `Regions` | `us` | Bookmaker regions (`us`, `uk`, `eu`, `au`) |
| `Markets` | `h2h,totals` | Comma-separated market keys to fetch |
| `OddsFormat` | `american` | `american` or `decimal` |
| `DefaultBookmakers` | `DraftKings, FanDuel, BetMGM` | Default bookmaker display filter |


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


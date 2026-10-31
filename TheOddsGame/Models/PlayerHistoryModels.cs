namespace OddsViewerApp.Models;

public class PlayerListItem
{
    public long PlayerId { get; set; }
    public string PlayerName { get; set; } = string.Empty;
    public string TeamName { get; set; } = string.Empty;
}

public class PlayerGameLog
{
    public long GameId { get; set; }
    public DateTime GameDate { get; set; }
    public string TeamName { get; set; } = string.Empty;
    public string OpponentName { get; set; } = string.Empty;
    public bool Home { get; set; }
    public bool? Win { get; set; }
    public decimal? TeamScore { get; set; }
    public decimal? OpponentScore { get; set; }
    public decimal? Minutes { get; set; }
    public decimal? Points { get; set; }
    public decimal? Rebounds { get; set; }
    public decimal? Assists { get; set; }
    public decimal? Steals { get; set; }
    public decimal? Blocks { get; set; }
    public decimal? Turnovers { get; set; }
    public decimal? PlusMinus { get; set; }
    public decimal? FieldGoalsMade { get; set; }
    public decimal? FieldGoalsAttempted { get; set; }
    public decimal? ThreePointersMade { get; set; }
    public decimal? ThreePointersAttempted { get; set; }
    public decimal? FreeThrowsMade { get; set; }
    public decimal? FreeThrowsAttempted { get; set; }

    public string Matchup => $"{(Home ? "vs" : "@")} {OpponentName}";

    public string Result => TeamScore.HasValue && OpponentScore.HasValue
        ? $"{(Win == true ? "W" : Win == false ? "L" : "")} {TeamScore:0}-{OpponentScore:0}".Trim()
        : string.Empty;
}

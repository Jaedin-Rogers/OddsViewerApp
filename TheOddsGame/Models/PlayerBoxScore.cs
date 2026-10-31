namespace OddsViewerApp.Models;

public class PlayerBoxScore
{
    public long GameId { get; set; }
    public DateTime GameDate { get; set; }
    public string TeamName { get; set; } = string.Empty;
    public bool Home { get; set; }
    public long PlayerId { get; set; }
    public string PlayerName { get; set; } = string.Empty;
    public string StartingPosition { get; set; } = string.Empty;
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
}

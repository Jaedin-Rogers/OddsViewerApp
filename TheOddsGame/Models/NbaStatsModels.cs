namespace OddsViewerApp.Models;

public class GameLogRow
{
    private string? _season;

    public long GameId { get; set; }
    public string Team { get; set; } = string.Empty;
    public string TeamAbbrev { get; set; } = string.Empty;
    public DateTime GameDate { get; set; }
    public string Opponent { get; set; } = string.Empty;
    public string OpponentAbbrev { get; set; } = string.Empty;
    public string Result { get; set; } = string.Empty;
    public bool Home { get; set; }
    public decimal? Points { get; set; }
    public decimal? OpponentPoints { get; set; }
    public decimal? Rebounds { get; set; }
    public decimal? Assists { get; set; }
    public decimal? Steals { get; set; }
    public decimal? Blocks { get; set; }
    public decimal? Turnovers { get; set; }
    public decimal? PlusMinus { get; set; }
    public decimal? FieldGoalsMade { get; set; }
    public decimal? FieldGoalsAttempted { get; set; }
    public decimal? FieldGoalsPercentage { get; set; }
    public decimal? ThreePointersMade { get; set; }
    public decimal? ThreePointersAttempted { get; set; }
    public decimal? ThreePointersPercentage { get; set; }
    public decimal? FreeThrowsMade { get; set; }
    public decimal? FreeThrowsAttempted { get; set; }
    public decimal? FreeThrowsPercentage { get; set; }
    public string GameType { get; set; } = string.Empty;

    public string Season
    {
        get => !string.IsNullOrWhiteSpace(_season) ? _season : GetNbaSeason(GameDate);
        set => _season = value;
    }

    public static string GetNbaSeason(DateTime date)
    {
        if (date == default) return string.Empty;
        var startYear = date.Month >= 10 ? date.Year : date.Year - 1;
        var endYearShort = (startYear + 1) % 100;
        return $"{startYear}-{endYearShort:D2}";
    }
}

public class PlayerLogRow
{
    private string? _season;

    public long GameId { get; set; }
    public long? PlayerId { get; set; }
    public double? PlayerIndex { get; set; }
    public string Player { get; set; } = string.Empty;
    public string FirstName { get; set; } = string.Empty;
    public string LastName { get; set; } = string.Empty;
    public string Team { get; set; } = string.Empty;
    public string TeamAbbrev { get; set; } = string.Empty;
    public DateTime GameDate { get; set; }
    public string Opponent { get; set; } = string.Empty;
    public string OpponentAbbrev { get; set; } = string.Empty;
    public string Result { get; set; } = string.Empty;
    public bool Home { get; set; }
    public string Minutes { get; set; } = string.Empty;
    public decimal? Points { get; set; }
    public decimal? Rebounds { get; set; }
    public decimal? Assists { get; set; }
    public decimal? Steals { get; set; }
    public decimal? Blocks { get; set; }
    public decimal? Turnovers { get; set; }
    public decimal? PlusMinus { get; set; }
    public decimal? ReboundsOffensive { get; set; }
    public decimal? ReboundsDefensive { get; set; }
    public decimal? FieldGoalsMade { get; set; }
    public decimal? FieldGoalsAttempted { get; set; }
    public decimal? FieldGoalsPercentage { get; set; }
    public decimal? ThreePointersMade { get; set; }
    public decimal? ThreePointersAttempted { get; set; }
    public decimal? ThreePointersPercentage { get; set; }
    public decimal? FreeThrowsMade { get; set; }
    public decimal? FreeThrowsAttempted { get; set; }
    public decimal? FreeThrowsPercentage { get; set; }
    public string GameType { get; set; } = string.Empty;

    public string Season
    {
        get => !string.IsNullOrWhiteSpace(_season) ? _season : GameLogRow.GetNbaSeason(GameDate);
        set => _season = value;
    }
}

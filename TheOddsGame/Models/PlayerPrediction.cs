using System.Text.Json.Serialization;

namespace OddsViewerApp.Models;

public sealed class PlayerPrediction
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }
    [JsonPropertyName("error")]
    public string? Error { get; set; }

    [JsonPropertyName("stat")]
    public string Stat { get; set; } = string.Empty;
    [JsonPropertyName("predicted")]
    public double Predicted { get; set; }
    [JsonPropertyName("line")]
    public double? Line { get; set; }
    [JsonPropertyName("p_over")]
    public double? POver { get; set; }
    [JsonPropertyName("p_under")]
    public double? PUnder { get; set; }
    [JsonPropertyName("lean")]
    public string? Lean { get; set; }
    [JsonPropertyName("selection")]
    public string? Selection { get; set; }
    [JsonPropertyName("p_pick")]
    public double? PPick { get; set; }

    [JsonPropertyName("position")]
    public string Position { get; set; } = string.Empty;
    [JsonPropertyName("is_home")]
    public bool IsHome { get; set; }
    [JsonPropertyName("games_in_window")]
    public int GamesInWindow { get; set; }
    [JsonPropertyName("avg_l5")]
    public double? AvgL5 { get; set; }
    [JsonPropertyName("avg_l10")]
    public double? AvgL10 { get; set; }
    [JsonPropertyName("avg_season")]
    public double? AvgSeason { get; set; }
    [JsonPropertyName("minutes_avg_l10")]
    public double? MinutesAvgL10 { get; set; }

    // Model probability for the selected side minus the book's implied probability (vig not removed).
    public double? Edge(decimal? americanPrice)
    {
        var implied = ImpliedProbability(americanPrice);
        return PPick.HasValue && implied.HasValue ? PPick.Value - implied.Value : null;
    }

    public static double? ImpliedProbability(decimal? americanPrice)
    {
        if (!americanPrice.HasValue || americanPrice.Value == 0) return null;
        var p = (double)americanPrice.Value;
        return p < 0 ? -p / (-p + 100d) : 100d / (p + 100d);
    }
}

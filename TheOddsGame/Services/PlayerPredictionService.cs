using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;
using OddsViewerApp.Models;

namespace OddsViewerApp.Services;

// Runs MLService/predict_player.py, which rebuilds pre-game features for the upcoming game and
// scores them with the trained models. One process handles a whole batch of bets.
public class PlayerPredictionService
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        NumberHandling = JsonNumberHandling.AllowReadingFromString
    };

    private static readonly HashSet<string> SupportedMarkets = new(StringComparer.OrdinalIgnoreCase)
    {
        "player_points", "player_points_alternate",
        "player_rebounds", "player_rebounds_alternate",
        "player_assists", "player_assists_alternate"
    };

    private readonly MlOptions _options;
    private readonly IWebHostEnvironment _env;
    private readonly ILogger<PlayerPredictionService> _logger;

    public PlayerPredictionService(IOptions<MlOptions> options, IWebHostEnvironment env, ILogger<PlayerPredictionService> logger)
    {
        _options = options.Value;
        _env = env;
        _logger = logger;
    }

    public static bool IsSupportedMarket(string? market) => market is not null && SupportedMarkets.Contains(market);

    // Returns one prediction per input row, in the same order. Unsupported markets get an error result
    // without being sent to Python.
    public async Task<List<PlayerPrediction>> PredictAsync(IReadOnlyList<OddsRow> rows, CancellationToken ct = default)
    {
        var results = new PlayerPrediction[rows.Count];
        var requests = new List<object>();
        var requestIndexes = new List<int>();

        for (var i = 0; i < rows.Count; i++)
        {
            var row = rows[i];
            if (!IsSupportedMarket(row.Market))
            {
                results[i] = new PlayerPrediction
                {
                    Error = $"'{row.Market}' is not supported yet (the model covers points, rebounds and assists)."
                };
                continue;
            }

            requestIndexes.Add(i);
            requests.Add(new Dictionary<string, object?>
            {
                ["player"] = row.Player,
                ["market"] = row.Market,
                ["line"] = row.Handicap,
                ["selection"] = row.Selection,
                ["home_team"] = row.HomeTeam,
                ["away_team"] = row.AwayTeam,
                ["match_date"] = row.MatchDate?.ToString("O", CultureInfo.InvariantCulture)
            });
        }

        if (requests.Count > 0)
        {
            var predictions = await RunPythonAsync(requests, ct);
            for (var j = 0; j < requestIndexes.Count; j++)
            {
                results[requestIndexes[j]] = j < predictions.Count
                    ? predictions[j]
                    : new PlayerPrediction { Error = "The model returned no result for this bet." };
            }
        }

        return results.ToList();
    }

    private async Task<List<PlayerPrediction>> RunPythonAsync(List<object> requests, CancellationToken ct)
    {
        var script = Path.IsPathRooted(_options.ScriptPath)
            ? _options.ScriptPath
            : Path.GetFullPath(Path.Combine(_env.ContentRootPath, _options.ScriptPath));

        if (!File.Exists(script))
            throw new FileNotFoundException($"Prediction script not found at {script}. Check Ml:ScriptPath.");

        var psi = new ProcessStartInfo(_options.PythonPath)
        {
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            WorkingDirectory = Path.GetDirectoryName(script)!,
            StandardOutputEncoding = Encoding.UTF8
        };
        psi.ArgumentList.Add(script);

        using var process = new Process { StartInfo = psi };
        var stderr = new StringBuilder();
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) stderr.AppendLine(e.Data); };

        try
        {
            process.Start();
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException(
                $"Could not start '{_options.PythonPath}'. Set Ml:PythonPath to a Python with scikit-learn installed.", ex);
        }

        process.BeginErrorReadLine();
        await process.StandardInput.WriteAsync(JsonSerializer.Serialize(requests));
        process.StandardInput.Close();

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(_options.TimeoutSeconds));

        try
        {
            var stdout = await process.StandardOutput.ReadToEndAsync(timeout.Token);
            await process.WaitForExitAsync(timeout.Token);

            if (process.ExitCode != 0)
            {
                _logger.LogError("predict_player.py failed ({Code}): {Err}", process.ExitCode, stderr);
                throw new InvalidOperationException($"Prediction script failed: {LastLines(stderr.ToString())}");
            }

            return JsonSerializer.Deserialize<List<PlayerPrediction>>(stdout, JsonOptions) ?? new();
        }
        catch (OperationCanceledException)
        {
            TryKill(process);
            throw new TimeoutException($"Prediction timed out after {_options.TimeoutSeconds}s.");
        }
    }

    private static void TryKill(Process process)
    {
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); } catch { /* already gone */ }
    }

    private static string LastLines(string text, int count = 3) =>
        string.Join(" | ", text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).TakeLast(count));
}

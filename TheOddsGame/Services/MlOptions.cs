namespace OddsViewerApp.Services;

public sealed class MlOptions
{
    // Interpreter that has scikit-learn (matching the version that saved the joblib) installed.
    public string PythonPath { get; set; } = "python3";

    // Relative paths resolve against the web project's content root.
    public string ScriptPath { get; set; } = "MLService/predict_player.py";

    public int TimeoutSeconds { get; set; } = 180;
}

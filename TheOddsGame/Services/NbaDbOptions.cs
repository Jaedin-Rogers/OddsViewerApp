using System.Text;

namespace OddsViewerApp.Services;

public sealed class NbaDbOptions
{
    public string? ConnectionString { get; set; }
    public string? Host { get; set; }
    public int Port { get; set; } = 5432;
    public string? Database { get; set; }
    public string? Username { get; set; }
    public string? Password { get; set; }
    public string SslMode { get; set; } = "Require";
    public bool TrustServerCertificate { get; set; } = true;

    public string BuildConnectionString()
    {
        if (!string.IsNullOrWhiteSpace(ConnectionString))
        {
            return ConnectionString;
        }

        if (string.IsNullOrWhiteSpace(Host) || string.IsNullOrWhiteSpace(Database) || string.IsNullOrWhiteSpace(Username))
        {
            return string.Empty;
        }

        var sb = new StringBuilder();
        sb.Append($"Host={Host};");
        sb.Append($"Port={Port};");
        sb.Append($"Database={Database};");
        sb.Append($"Username={Username};");
        if (!string.IsNullOrEmpty(Password))
        {
            sb.Append($"Password={Password};");
        }
        sb.Append($"SSL Mode={SslMode};");
        sb.Append($"Trust Server Certificate={TrustServerCertificate};");

        return sb.ToString();
    }
}

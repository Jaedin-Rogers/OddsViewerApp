using MudBlazor;


namespace BlazorApp2.Components;


public static class Theme
{
    public static MudTheme ApplicationTheme()
    {
        return new MudTheme
        {
            PaletteLight = new PaletteLight
            {
                Primary = "#0F3D70",
                Secondary = "#ED780F",
                Success = "#198754",
                Info = "#0dcaf0",
                Warning = "#ffc107",
                Error = "#dc3545",
                Background = "#f8f9fa",
                Surface = "#ffffff",
                AppbarBackground = "#ffffff",
                AppbarText = "#212529",
                DrawerBackground = "#ffffff",
                DrawerText = "#212529",
                TextPrimary = "#212529",
                TextSecondary = "#6c757d",
                Divider = "#dee2e6",
                TableLines = "#dee2e6"
            },
            PaletteDark = new PaletteDark
            {
                Primary = "#5090CD",
                Secondary = "#ED780F",
                Success = "#4caf50",
                Info = "#29b6f6",
                Warning = "#ffd54f",
                Error = "#ff5252",
                Background = "#1e1e1e",
                Surface = "#2a2a2a",
                AppbarBackground = "#2a2a2a",
                AppbarText = "#f8f9fa",
                DrawerBackground = "#2a2a2a",
                DrawerText = "#f8f9fa",
                TextPrimary = "#f8f9fa",
                TextSecondary = "#adb5bd",
                Divider = "#343a40",
                TableLines = "#343a40"
            },
            LayoutProperties = new LayoutProperties
            {
                DrawerWidthLeft = "260px",
                AppbarHeight = "64px",
                DefaultBorderRadius = "8px"
            }
        };
    }
}
class ThemeManager:
    """Manages light and dark theme colors for the ForensicPack application."""
    
    _themes = {
        "dark": {
            "BG": "#0d1117",
            "BG2": "#161b22",
            "BG3": "#21262d",
            "BORDER": "#30363d",
            "FG": "#c9d1d9",
            "FG2": "#8b949e",
            "ACCENT": "#58a6ff",
            "GREEN": "#3fb950",
            "YELLOW": "#d29922",
            "RED": "#f85149",
            "WHITE": "#f0f6fc",
            "PURPLE": "#bc8cff",
        },
        "light": {
            "BG": "#ffffff",
            "BG2": "#f6f8fa",
            "BG3": "#eaeef2",
            "BORDER": "#d0d7de",
            "FG": "#24292f",
            "FG2": "#57606a",
            "ACCENT": "#0969da",
            "GREEN": "#1a7f37",
            "YELLOW": "#9a6700",
            "RED": "#cf222e",
            "WHITE": "#24292f",
            "PURPLE": "#8250df",
        }
    }

    def __init__(self, theme_name="dark"):
        self.current_theme_name = theme_name if theme_name in self._themes else "dark"

    def get_color(self, name: str) -> str:
        return self._themes[self.current_theme_name].get(name, "#000000")

    def toggle_theme(self):
        self.current_theme_name = "light" if self.current_theme_name == "dark" else "dark"

    @property
    def BG(self) -> str: return self.get_color("BG")
    @property
    def BG2(self) -> str: return self.get_color("BG2")
    @property
    def BG3(self) -> str: return self.get_color("BG3")
    @property
    def BORDER(self) -> str: return self.get_color("BORDER")
    @property
    def FG(self) -> str: return self.get_color("FG")
    @property
    def FG2(self) -> str: return self.get_color("FG2")
    @property
    def ACCENT(self) -> str: return self.get_color("ACCENT")
    @property
    def GREEN(self) -> str: return self.get_color("GREEN")
    @property
    def YELLOW(self) -> str: return self.get_color("YELLOW")
    @property
    def RED(self) -> str: return self.get_color("RED")
    @property
    def WHITE(self) -> str: return self.get_color("WHITE")
    @property
    def PURPLE(self) -> str: return self.get_color("PURPLE")

# Global theme instance
theme = ThemeManager()

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from gui_components.themes import theme

FONT_MAIN = ("Segoe UI", 10)
FONT_HEAD = ("Segoe UI Semibold", 11)
FONT_MONO = ("Consolas", 9)
FONT_TITLE = ("Segoe UI Semibold", 15)

class ScrollableFrame(tk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        bg_col = kwargs.get("bg", theme.BG)
        self.canvas = tk.Canvas(self, bg=bg_col, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scroll_window = tk.Frame(self.canvas, bg=bg_col)
        self.scroll_window.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scroll_window, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self._mousewheel_active = False

        def _on_mousewheel(event):
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = int(-1 * (event.delta / 120)) if event.delta else 0
            if delta:
                self.canvas.yview_scroll(delta, "units")
                return "break"
            return None

        def _enable_mousewheel(_event=None):
            if self._mousewheel_active:
                return
            self._mousewheel_active = True
            self.bind_all("<MouseWheel>", _on_mousewheel)
            self.bind_all("<Button-4>", _on_mousewheel)
            self.bind_all("<Button-5>", _on_mousewheel)

        def _disable_mousewheel(_event=None):
            if not self._mousewheel_active:
                return
            self._mousewheel_active = False
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        for widget in (self.canvas, self.scroll_window, self.scrollbar):
            widget.bind("<Enter>", _enable_mousewheel)
            widget.bind("<Leave>", _disable_mousewheel)
            
    def update_theme(self):
        bg_col = theme.BG
        self.config(bg=bg_col)
        self.canvas.config(bg=bg_col)
        self.scroll_window.config(bg=bg_col)

import re

def natural_text_key(s: str) -> tuple[object, ...]:
    return tuple(int(t) if t.isdigit() else t for t in re.split(r'(\d+)', s.lower()))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import math

from engine import ARCHIVE_FORMATS, COMPRESSION_LEVELS, HASH_NAMES
from gui_components.themes import theme
from gui_components.common import FONT_MAIN, FONT_HEAD

def register_mutable(app, widget, enabled_state="normal", disabled_state="disabled"):
    from gui_state import WidgetStateBinding
    app._mutable_bindings.append(
        WidgetStateBinding(widget=widget, enabled_state=enabled_state, disabled_state=disabled_state)
    )

def create_card(parent, title):
    outer = ttk.Frame(parent, style="Card.TFrame")
    outer.pack(fill="x", pady=(0, 10))
    header = tk.Label(outer, text=title, font=FONT_HEAD, fg=theme.ACCENT, bg=theme.BG2)
    header.pack(anchor="w", padx=14, pady=(10, 4))
    line = tk.Frame(outer, bg=theme.BORDER, height=1)
    line.pack(fill="x", padx=14, pady=(0, 8))
    inner = tk.Frame(outer, bg=theme.BG2)
    inner.pack(fill="x", padx=14, pady=(0, 12))
    
    # Store references for theme updates
    outer._theme_widgets = [header, line, inner]
    return inner

def browse_btn(app, parent, var):
    def _pick():
        selected = filedialog.askdirectory(mustexist=False)
        if selected:
            var.set(selected)
    btn = tk.Button(parent, text="Browse ...", command=_pick, bg=theme.BG3, fg=theme.ACCENT, font=FONT_MAIN, relief="flat", activebackground=theme.ACCENT, activeforeground=theme.BG, cursor="hand2", padx=10)
    btn.pack(side="right", padx=(6, 0))
    register_mutable(app, btn)
    return btn

def build_paths_panel(app, parent):
    inner = create_card(parent, "Source & Destination")
    app._src_var = tk.StringVar(value=str(app._settings.get("source_dir", "")))
    app._dst_var = tk.StringVar(value=str(app._settings.get("output_dir", "")))
    
    app._theme_labels = []
    app._theme_entries = []
    
    for label, var in [("Source Folder:", app._src_var), ("Output Folder:", app._dst_var)]:
        row = tk.Frame(inner, bg=theme.BG2)
        row.pack(fill="x", pady=3)
        lbl = tk.Label(row, text=label, width=16, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN)
        lbl.pack(side="left")
        app._theme_labels.append(lbl)
        
        entry = tk.Entry(row, textvariable=var, bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, relief="flat", font=FONT_MAIN, highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        entry.pack(side="left", fill="x", expand=True)
        app._theme_entries.append(entry)
        
        register_mutable(app, entry)
        browse_btn(app, row, var)

    app._delete_src_var = tk.BooleanVar(value=bool(app._settings.get("delete_source", False)))
    app._delete_cb = tk.Checkbutton(inner, text="Delete source item after successful verification", variable=app._delete_src_var, bg=theme.BG2, fg=theme.RED, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=FONT_MAIN, cursor="hand2")
    app._delete_cb.pack(anchor="w", pady=(6, 0))
    register_mutable(app, app._delete_cb)
    
    app._skip_existing_var = tk.BooleanVar(value=bool(app._settings.get("skip_existing", False)))
    app._skip_cb = tk.Checkbutton(inner, text="Skip existing archives only if they verify cleanly", variable=app._skip_existing_var, bg=theme.BG2, fg=theme.FG, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=FONT_MAIN, cursor="hand2")
    app._skip_cb.pack(anchor="w", pady=(4, 0))
    register_mutable(app, app._skip_cb)
    
    selector_row = tk.Frame(inner, bg=theme.BG2)
    selector_row.pack(fill="x", pady=(6, 0))
    
    select_btn = tk.Button(
        selector_row, text="Scan & Select Items ...", command=app._open_item_selector,
        bg=theme.BG3, fg=theme.ACCENT, font=FONT_MAIN, relief="flat",
        activebackground=theme.ACCENT, activeforeground=theme.BG, cursor="hand2", padx=10
    )
    select_btn.pack(side="left")
    register_mutable(app, select_btn)
    
    clear_btn = tk.Button(
        selector_row, text="Use All", command=app._clear_item_selection,
        bg=theme.BG3, fg=theme.FG2, font=FONT_MAIN, relief="flat",
        activebackground=theme.BORDER, activeforeground=theme.WHITE, cursor="hand2", padx=10
    )
    clear_btn.pack(side="left", padx=(8, 0))
    register_mutable(app, clear_btn)
    
    reset_settings_btn = tk.Button(
        selector_row, text="Reset Saved", command=app._reset_saved_settings,
        bg=theme.BG3, fg=theme.YELLOW, font=FONT_MAIN, relief="flat",
        activebackground=theme.YELLOW, activeforeground=theme.BG, cursor="hand2", padx=10
    )
    reset_settings_btn.pack(side="left", padx=(8, 0))
    register_mutable(app, reset_settings_btn)
    
    app._selected_items_var = tk.StringVar()
    app._update_selected_items_label()
    
    app._selected_lbl = tk.Label(inner, textvariable=app._selected_items_var, fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left")
    app._selected_lbl.pack(anchor="w", pady=(4, 0))
    
    app._desc_lbl1 = tk.Label(
        inner,
        text="Basic step 1: choose source/output, then optionally scan and pick specific direct children to process.",
        fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left"
    )
    app._desc_lbl1.pack(anchor="w", pady=(4, 0))


def build_archive_panel(app, parent):
    inner = create_card(parent, "Basic Archive Setup")
    
    row1 = tk.Frame(inner, bg=theme.BG2)
    row1.pack(fill="x", pady=3)
    lbl1 = tk.Label(row1, text="Format:", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN)
    lbl1.pack(side="left")
    app._theme_labels.append(lbl1)
    
    app._fmt_var = tk.StringVar(value=str(app._settings.get("archive_fmt", ARCHIVE_FORMATS[0])))
    format_cb = ttk.Combobox(row1, textvariable=app._fmt_var, values=ARCHIVE_FORMATS, state="readonly", width=14)
    format_cb.pack(side="left")
    format_cb.bind("<<ComboboxSelected>>", app._on_format_changed)
    register_mutable(app, format_cb, enabled_state="readonly", disabled_state="disabled")

    row2 = tk.Frame(inner, bg=theme.BG2)
    row2.pack(fill="x", pady=3)
    lbl2 = tk.Label(row2, text="Compression:", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN)
    lbl2.pack(side="left")
    app._theme_labels.append(lbl2)
    
    app._level_var = tk.StringVar(value=str(app._settings.get("compress_level_label", "Normal (5)")))
    level_cb = ttk.Combobox(row2, textvariable=app._level_var, values=list(COMPRESSION_LEVELS.keys()), state="readonly", width=22)
    level_cb.pack(side="left")
    register_mutable(app, level_cb, enabled_state="readonly", disabled_state="disabled")

    app._desc_lbl2 = tk.Label(inner, text="Basic step 2: choose archive format and compression. Advanced behavior is below.", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left")
    app._desc_lbl2.pack(anchor="w", pady=(4, 8))


def format_duration(seconds: float) -> str:
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    minutes = minutes % 60
    return f"{hours}h {minutes}m {secs}s"

def estimate_eta_seconds(start_time: float, fraction_complete: float) -> float | None:
    if fraction_complete <= 0.001:
        return None
    import time
    elapsed = time.monotonic() - start_time
    total_est = elapsed / fraction_complete
    return max(0.0, total_est - elapsed)

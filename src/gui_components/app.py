import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from gui_components.themes import theme
from gui_components.common import FONT_MAIN, FONT_HEAD, FONT_MONO, FONT_TITLE, ScrollableFrame, natural_text_key
from gui_components.panels import estimate_eta_seconds, format_duration, build_paths_panel, build_archive_panel

from engine import (
    APP_AUTHOR,
    APP_NAME,
    APP_VERSION,
    ARCHIVE_FORMATS,
    COMPRESSION_LEVELS,
    HASH_NAMES,
    CancellationToken,
    JobCallbacks,
    JobConfig,
    find_7zip,
    run_session,
)

from gui_assets import TITLE_BANNER_CANDIDATES, resolve_first_existing_gui_asset, resolve_gui_asset_path
from gui_state import (
    WidgetStateBinding,
    apply_widget_bindings,
    build_run_summary,
    estimate_eta_seconds,
    format_duration,
    load_gui_settings,
    matches_queue_filter,
    queue_filter_counts,
    requires_destructive_confirmation,
    save_gui_settings,
    settings_path,
    validate_destructive_confirmation,
    build_windows_elevation_command,
    is_permission_error,
)


class ForensicPackApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1140x680")
        self.minsize(1000, 600)
        self.configure(bg=theme.BG)
        self._window_icon_image: tk.PhotoImage | None = None
        self._title_banner_image: tk.PhotoImage | None = None
        self._selected_item_names: list[str] | None = None
        self._settings = load_gui_settings()
        self._thread = None
        self._token: CancellationToken | None = None
        self._skip_archive_hash_cb: tk.Checkbutton | None = None
        self._ui_queue: queue.Queue[tuple] = queue.Queue()
        self._queue_rows: list[dict[str, object]] = []
        self._running_jobs: set[int] = set()
        self._queue_filter = "All"
        self._queue_filter_buttons: dict[str, tk.Button] = {}
        self._mutable_bindings: list[WidgetStateBinding] = []
        self._run_started_at: float | None = None
        self._last_elapsed_seconds = 0.0
        self._current_phase = "idle"
        self._last_report_path = ""
        self._last_output_dir = ""
        self._apply_window_icon()
        self._build_ui()
        self._apply_style()
        self.after(50, self._drain_ui_queue)

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=theme.BG)
        style.configure("Card.TFrame", background=theme.BG2, relief="flat")
        style.configure("TLabel", background=theme.BG, foreground=theme.FG, font=FONT_MAIN)
        style.configure("Horizontal.TProgressbar", troughcolor=theme.BG3, background=theme.ACCENT, lightcolor=theme.ACCENT, darkcolor=theme.ACCENT, bordercolor=theme.BORDER)
        style.configure("Green.Horizontal.TProgressbar", troughcolor=theme.BG3, background=theme.GREEN, lightcolor=theme.GREEN, darkcolor=theme.GREEN, bordercolor=theme.BORDER)
        style.configure("Yellow.Horizontal.TProgressbar", troughcolor=theme.BG3, background=theme.YELLOW, lightcolor=theme.YELLOW, darkcolor=theme.YELLOW, bordercolor=theme.BORDER)
        style.configure("Red.Horizontal.TProgressbar", troughcolor=theme.BG3, background=theme.RED, lightcolor=theme.RED, darkcolor=theme.RED, bordercolor=theme.BORDER)

    def _build_ui(self):
        title_frame = tk.Frame(self, bg=theme.BG2, height=60)
        title_frame.pack(fill="x", side="top")
        title_frame.pack_propagate(False)
        has_banner = self._build_title_brand(title_frame)
        if not has_banner:
            tk.Label(title_frame, text=APP_NAME, font=FONT_TITLE, fg=theme.WHITE, bg=theme.BG2).pack(side="left", pady=10, padx=(0, 2))
        tk.Label(title_frame, text=f"v{APP_VERSION}  .  {APP_AUTHOR}", font=FONT_MAIN, fg=theme.FG2, bg=theme.BG2).pack(side="left", padx=10, pady=10)
        self._theme_btn = tk.Button(
            title_frame, text="\u263e", font=("Segoe UI", 14), fg=theme.FG2, bg=theme.BG2,
            relief="flat", bd=0, activebackground=theme.BG3, activeforeground=theme.WHITE,
            cursor="hand2", command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=8, pady=10)
        tk.Label(title_frame, text="DFIR Auto-Archiver", font=("Segoe UI", 9), fg=theme.PURPLE, bg=theme.BG2).pack(side="right", padx=18, pady=10)

        footer = tk.Frame(self, bg=theme.BG, bd=0, highlightthickness=1, highlightbackground=theme.BORDER)
        footer.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        self._build_controls(footer)

        self._paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self._paned.pack(fill="both", expand=True, padx=16, pady=(12, 4))
        left_container = ttk.Frame(self._paned)
        self._paned.add(left_container, weight=1)
        scroll_frame = ScrollableFrame(left_container, bg=theme.BG)
        scroll_frame.pack(fill="both", expand=True)
        left = scroll_frame.scroll_window
        right = ttk.Frame(self._paned)
        self._paned.add(right, weight=2)
        self._left_root = left

        tk.Label(left, text="Basic Workflow", font=("Segoe UI", 10, "bold"), fg=theme.WHITE, bg=theme.BG).pack(anchor="w", pady=(2, 6))
        self._build_paths_panel(left)
        self._build_archive_panel(left)
        self._build_hash_panel(left)

        tk.Label(left, text="Advanced Options", font=("Segoe UI", 10, "bold"), fg=theme.WHITE, bg=theme.BG).pack(anchor="w", pady=(6, 6))
        self._build_advanced_options_panel(left)
        self._build_case_panel(left)
        self._build_queue_panel(right)
        self._build_log_panel(right)

    def _build_title_brand(self, parent) -> bool:
        banner = self._load_title_banner()
        if banner is None:
            self._build_title_icon(parent)
            return False
        tk.Label(parent, image=banner, bg=theme.BG2, bd=0, highlightthickness=0).pack(side="left", padx=(16, 8), pady=10)
        return True

    def _load_title_banner(self) -> tk.PhotoImage | None:
        banner_path = resolve_first_existing_gui_asset(TITLE_BANNER_CANDIDATES)
        if banner_path is None:
            return None
        try:
            banner_image = tk.PhotoImage(file=str(banner_path))
        except tk.TclError:
            return None
        max_width = 260
        max_height = 38
        scale_x = max(1, (banner_image.width() + max_width - 1) // max_width)
        scale_y = max(1, (banner_image.height() + max_height - 1) // max_height)
        scale = max(scale_x, scale_y)
        if scale > 1:
            banner_image = banner_image.subsample(scale, scale)
        self._title_banner_image = banner_image
        return self._title_banner_image

    def _build_title_icon(self, parent):
        steel_fill = "#536070"
        steel_line = "#e6edf3"
        steel_accent = "#8aa8c7"
        icon = tk.Canvas(parent, width=38, height=32, bg=theme.BG2, highlightthickness=0, bd=0)
        icon.create_polygon(
            7,
            7,
            25,
            7,
            31,
            13,
            31,
            25,
            7,
            25,
            fill=steel_fill,
            outline=steel_line,
            width=2,
        )
        icon.create_oval(11, 11, 15, 15, fill=theme.BG2, outline=steel_line, width=1)
        icon.create_line(15, 18, 26, 18, fill=steel_line, width=2)
        icon.create_line(15, 21.5, 22, 21.5, fill=steel_accent, width=2)
        icon.pack(side="left", padx=(16, 10), pady=10)

    def _asset_path(self, filename: str) -> Path:
        return resolve_gui_asset_path(filename)

    def _apply_window_icon(self) -> None:
        png_path = self._asset_path("forensicpack_icon.png")
        ico_path = self._asset_path("forensicpack_icon.ico")
        if sys.platform.startswith("win") and ico_path.is_file():
            try:
                self.iconbitmap(default=str(ico_path))
            except tk.TclError:
                pass
        if png_path.is_file():
            try:
                self._window_icon_image = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, self._window_icon_image)
            except tk.TclError:
                self._window_icon_image = None

    def _register_mutable(self, widget, enabled_state="normal", disabled_state="disabled"):
        self._mutable_bindings.append(
            WidgetStateBinding(widget=widget, enabled_state=enabled_state, disabled_state=disabled_state)
        )

    def _set_mutable_controls_enabled(self, enabled: bool) -> None:
        apply_widget_bindings(self._mutable_bindings, enabled=enabled)

    def _card(self, parent, title):
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.pack(fill="x", pady=(0, 10))
        tk.Label(outer, text=title, font=FONT_HEAD, fg=theme.ACCENT, bg=theme.BG2).pack(anchor="w", padx=14, pady=(10, 4))
        tk.Frame(outer, bg=theme.BORDER, height=1).pack(fill="x", padx=14, pady=(0, 8))
        inner = tk.Frame(outer, bg=theme.BG2)
        inner.pack(fill="x", padx=14, pady=(0, 12))
        return inner

    def _browse_btn(self, parent, var):
        def _pick():
            selected = filedialog.askdirectory(mustexist=False)
            if selected:
                var.set(selected)
        btn = tk.Button(parent, text="Browse ...", command=_pick, bg=theme.BG3, fg=theme.ACCENT, font=FONT_MAIN, relief="flat", activebackground=theme.ACCENT, activeforeground=theme.BG, cursor="hand2", padx=10)
        btn.pack(side="right", padx=(6, 0))
        self._register_mutable(btn)
        return btn

    def _browse_state_db_btn(self, parent):
        def _pick():
            initial_output = self._dst_var.get().strip() if hasattr(self, "_dst_var") else ""
            initial_dir = initial_output or str(Path.home())
            initial_file = "forensicpack_state.db"
            current = self._state_db_var.get().strip() if hasattr(self, "_state_db_var") else ""
            if current:
                current_path = Path(current)
                initial_dir = str(current_path.parent)
                initial_file = current_path.name
            selected = filedialog.asksaveasfilename(
                title="Select State DB Path",
                initialdir=initial_dir,
                initialfile=initial_file,
                defaultextension=".db",
                filetypes=[("SQLite DB", "*.db *.sqlite *.sqlite3"), ("All Files", "*.*")],
            )
            if selected:
                self._state_db_var.set(selected)

        btn = tk.Button(
            parent,
            text="Browse ...",
            command=_pick,
            bg=theme.BG3,
            fg=theme.ACCENT,
            font=FONT_MAIN,
            relief="flat",
            activebackground=theme.ACCENT,
            activeforeground=theme.BG,
            cursor="hand2",
            padx=10,
        )
        btn.pack(side="right", padx=(6, 0))
        self._register_mutable(btn)
        return btn

    def _clear_state_db_btn(self, parent):
        btn = tk.Button(
            parent,
            text="Default",
            command=lambda: self._state_db_var.set(""),
            bg=theme.BG3,
            fg=theme.FG2,
            font=FONT_MAIN,
            relief="flat",
            activebackground=theme.BORDER,
            activeforeground=theme.WHITE,
            cursor="hand2",
            padx=10,
        )
        btn.pack(side="right", padx=(6, 0))
        self._register_mutable(btn)
        return btn

    def _help_btn(self, parent, title, message):
        btn = tk.Button(
            parent, text="ⓘ", font=("Segoe UI", 10), fg=theme.FG2, bg=theme.BG2,
            relief="flat", bd=0, activebackground=theme.BG2, activeforeground=theme.ACCENT,
            cursor="hand2", command=lambda: messagebox.showinfo(title, message)
        )
        btn.pack(side="left", padx=(4, 0))
        return btn

    def _labeled_entry(self, parent, label, var, show=None):
        row = tk.Frame(parent, bg=theme.BG2)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        entry = tk.Entry(row, textvariable=var, show=show, bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, relief="flat", font=FONT_MAIN, highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        entry.pack(side="left", fill="x", expand=True)
        self._register_mutable(entry)
        return entry

    def _build_paths_panel(self, parent):
        inner = self._card(parent, "Source & Destination")
        self._src_var = tk.StringVar(value=str(self._settings.get("source_dir", "")))
        self._dst_var = tk.StringVar(value=str(self._settings.get("output_dir", "")))
        for label, var in [("Source Folder:", self._src_var), ("Output Folder:", self._dst_var)]:
            row = tk.Frame(inner, bg=theme.BG2)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, width=16, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
            entry = tk.Entry(row, textvariable=var, bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, relief="flat", font=FONT_MAIN, highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
            entry.pack(side="left", fill="x", expand=True)
            self._register_mutable(entry)
            self._browse_btn(row, var)

        self._delete_src_var = tk.BooleanVar(value=bool(self._settings.get("delete_source", False)))
        delete_row = tk.Frame(inner, bg=theme.BG2)
        delete_row.pack(fill="x", pady=(6, 0))
        delete_cb = tk.Checkbutton(delete_row, text="Delete source item after successful verification", variable=self._delete_src_var, bg=theme.BG2, fg=theme.RED, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=FONT_MAIN, cursor="hand2")
        delete_cb.pack(side="left")
        self._help_btn(delete_row, "Delete Source", "After successful archive verification, the original source item is permanently removed. Use only when your retention policy allows it.")
        self._register_mutable(delete_cb)
        self._skip_existing_var = tk.BooleanVar(value=bool(self._settings.get("skip_existing", False)))
        skip_row = tk.Frame(inner, bg=theme.BG2)
        skip_row.pack(fill="x", pady=(4, 0))
        skip_cb = tk.Checkbutton(skip_row, text="Skip existing archives only if they verify cleanly", variable=self._skip_existing_var, bg=theme.BG2, fg=theme.FG, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=FONT_MAIN, cursor="hand2")
        skip_cb.pack(side="left")
        self._help_btn(skip_row, "Skip Existing Archives", "When enabled, ForensicPack verifies an existing output archive first. If verification passes, that item is skipped instead of repacked.")
        self._register_mutable(skip_cb)
        selector_row = tk.Frame(inner, bg=theme.BG2)
        selector_row.pack(fill="x", pady=(6, 0))
        select_btn = tk.Button(
            selector_row,
            text="Scan & Select Items ...",
            command=self._open_item_selector,
            bg=theme.BG3,
            fg=theme.ACCENT,
            font=FONT_MAIN,
            relief="flat",
            activebackground=theme.ACCENT,
            activeforeground=theme.BG,
            cursor="hand2",
            padx=10,
        )
        select_btn.pack(side="left")
        self._register_mutable(select_btn)
        clear_btn = tk.Button(
            selector_row,
            text="Use All",
            command=self._clear_item_selection,
            bg=theme.BG3,
            fg=theme.FG2,
            font=FONT_MAIN,
            relief="flat",
            activebackground=theme.BORDER,
            activeforeground=theme.WHITE,
            cursor="hand2",
            padx=10,
        )
        clear_btn.pack(side="left", padx=(8, 0))
        self._register_mutable(clear_btn)
        self._help_btn(selector_row, "Scan & Select Items", "Lets you choose only specific source children for this run. Use 'Use All' to clear filtering.")
        reset_settings_btn = tk.Button(
            selector_row,
            text="Reset Saved Settings",
            command=self._reset_saved_settings,
            bg=theme.BG3,
            fg=theme.YELLOW,
            font=FONT_MAIN,
            relief="flat",
            activebackground=theme.YELLOW,
            activeforeground=theme.BG,
            cursor="hand2",
            padx=10,
        )
        reset_settings_btn.pack(side="left", padx=(8, 0))
        self._register_mutable(reset_settings_btn)
        self._selected_items_var = tk.StringVar()
        self._update_selected_items_label()
        tk.Label(inner, textvariable=self._selected_items_var, fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(4, 0))
        tk.Label(
            inner,
            text="Basic step 1: choose source/output, then optionally scan and pick specific direct children to process.",
            fg=theme.FG2,
            bg=theme.BG2,
            font=("Segoe UI", 9),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def _clear_item_selection(self):
        self._selected_item_names = None
        self._update_selected_items_label()

    def _reset_saved_settings(self):
        if not messagebox.askyesno("Reset Saved Settings", "Clear persisted GUI settings and blank Source/Output fields?"):
            return
        settings_file = settings_path()
        try:
            if settings_file.exists():
                settings_file.unlink()
        except OSError as exc:
            messagebox.showerror("Reset Failed", f"Could not remove settings file:\n{exc}")
            return
        self._src_var.set("")
        self._dst_var.set("")
        self._selected_item_names = None
        self._update_selected_items_label()
        self._last_report_path = ""
        self._last_output_dir = ""
        self._status_var.set("Saved settings reset.")
        messagebox.showinfo("Settings Reset", f"Saved settings were reset.\n{settings_file}")

    def _update_selected_items_label(self):
        if self._selected_item_names is None:
            self._selected_items_var.set("Selection: All direct children")
            return
        if not self._selected_item_names:
            self._selected_items_var.set("Selection: None")
            return
        if len(self._selected_item_names) <= 3:
            selected = ", ".join(self._selected_item_names)
        else:
            preview = ", ".join(self._selected_item_names[:3])
            selected = f"{preview}, +{len(self._selected_item_names) - 3} more"
        self._selected_items_var.set(f"Selection ({len(self._selected_item_names)}): {selected}")

    def _scan_source_items(self) -> list[Path]:
        source_text = self._src_var.get().strip()
        if not source_text:
            raise ValueError("Please select a source folder first.")
        source_dir = Path(source_text)
        if not source_dir.is_dir():
            raise ValueError(f"Source folder not found: {source_dir}")
        return sorted(source_dir.iterdir(), key=lambda item: item.name.lower())

    def _open_item_selector(self):
        try:
            items = self._scan_source_items()
        except ValueError as exc:
            messagebox.showerror("Source Required", str(exc))
            return
        if not items:
            messagebox.showinfo("No Items", "No direct children were found in the source folder.")
            return

        selected_lookup = set(self._selected_item_names or [item.name for item in items])
        chosen: list[str] | None = None
        dialog = tk.Toplevel(self)
        dialog.title("Select Source Items")
        dialog.configure(bg=theme.BG2)
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("540x420")
        dialog.minsize(460, 360)

        header = tk.Label(
            dialog,
            text="Select which direct children from the source folder should be processed.",
            fg=theme.FG,
            bg=theme.BG2,
            font=FONT_MAIN,
            wraplength=500,
            justify="left",
        )
        header.pack(anchor="w", padx=14, pady=(12, 8))

        sort_row = tk.Frame(dialog, bg=theme.BG2)
        sort_row.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(sort_row, text="Sort:", fg=theme.FG2, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        sort_var = tk.StringVar(value="Name (A-Z)")
        sort_cb = ttk.Combobox(
            sort_row,
            textvariable=sort_var,
            values=["Name (A-Z)", "Name (Z-A)", "Folders First (A-Z)", "Folders First (Z-A)"],
            state="readonly",
            width=20,
        )
        sort_cb.pack(side="left", padx=(6, 0))

        list_frame = tk.Frame(dialog, bg=theme.BG2)
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        canvas = tk.Canvas(list_frame, bg=theme.BG2, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        items_frame = tk.Frame(canvas, bg=theme.BG2)
        canvas_window = canvas.create_window((0, 0), window=items_frame, anchor="nw")
        items_frame.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        check_vars = {item.name: tk.BooleanVar(value=item.name in selected_lookup) for item in items}
        count_var = tk.StringVar()

        def _refresh_count():
            selected_count = sum(1 for var in check_vars.values() if var.get())
            count_var.set(f"Selected: {selected_count} / {len(items)}")

        def _natural_name_key(item: Path) -> tuple[object, ...]:
            parts = re.split(r"(\d+)", item.name.lower())
            return tuple(int(part) if part.isdigit() else part for part in parts)

        def _sorted_items() -> list[Path]:
            mode = sort_var.get()
            if mode == "Name (Z-A)":
                return sorted(items, key=_natural_name_key, reverse=True)
            if mode == "Folders First (A-Z)":
                return sorted(items, key=lambda item: (not item.is_dir(), _natural_name_key(item)))
            if mode == "Folders First (Z-A)":
                by_name = sorted(items, key=_natural_name_key, reverse=True)
                return sorted(by_name, key=lambda item: not item.is_dir())
            return sorted(items, key=_natural_name_key)

        def _render_items():
            for widget in items_frame.winfo_children():
                widget.destroy()
            for item in _sorted_items():
                row = tk.Frame(items_frame, bg=theme.BG2)
                row.pack(fill="x", pady=1)
                prefix = "[DIR]" if item.is_dir() else "[FILE]"
                cb = tk.Checkbutton(
                    row,
                    text=f"{prefix} {item.name}",
                    variable=check_vars[item.name],
                    command=_refresh_count,
                    bg=theme.BG2,
                    fg=theme.WHITE,
                    activebackground=theme.BG2,
                    activeforeground=theme.WHITE,
                    selectcolor=theme.BG3,
                    font=FONT_MAIN,
                    anchor="w",
                    relief="flat",
                    padx=6,
                    cursor="hand2",
                )
                cb.pack(fill="x")
            _refresh_count()

        def _on_selector_wheel(event):
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = int(-1 * (event.delta / 120)) if event.delta else 0
            if delta:
                canvas.yview_scroll(delta, "units")
                return "break"
            return None

        for widget in (canvas, items_frame):
            widget.bind("<MouseWheel>", _on_selector_wheel)
            widget.bind("<Button-4>", _on_selector_wheel)
            widget.bind("<Button-5>", _on_selector_wheel)

        sort_cb.bind("<<ComboboxSelected>>", lambda _e: _render_items())
        _render_items()
        tk.Label(dialog, textvariable=count_var, fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 8))

        controls = tk.Frame(dialog, bg=theme.BG2)
        controls.pack(fill="x", padx=14, pady=(0, 12))

        def _select_all():
            for var in check_vars.values():
                var.set(True)
            _refresh_count()

        def _clear_all():
            for var in check_vars.values():
                var.set(False)
            _refresh_count()

        def _apply():
            nonlocal chosen
            selected_names = [item.name for item in items if check_vars[item.name].get()]
            if not selected_names:
                messagebox.showwarning("No Items Selected", "Select at least one item or click Use All.")
                return
            chosen = selected_names
            dialog.destroy()

        tk.Button(controls, text="Select All", command=_select_all, bg=theme.BG3, fg=theme.FG, relief="flat", cursor="hand2", padx=10).pack(side="left")
        tk.Button(controls, text="Clear", command=_clear_all, bg=theme.BG3, fg=theme.FG, relief="flat", cursor="hand2", padx=10).pack(side="left", padx=(6, 0))
        tk.Button(controls, text="Cancel", command=dialog.destroy, bg=theme.BG3, fg=theme.FG, relief="flat", cursor="hand2", padx=12).pack(side="right")
        tk.Button(controls, text="Apply Selection", command=_apply, bg=theme.ACCENT, fg=theme.BG, relief="flat", cursor="hand2", padx=12).pack(side="right", padx=(0, 8))

        self.wait_window(dialog)
        if chosen is not None:
            self._selected_item_names = chosen
            self._update_selected_items_label()

    def _resolved_selected_items(self, source_dir: Path) -> list[str] | None:
        if self._selected_item_names is None:
            return None
        names_by_lower = {item.name.lower(): item.name for item in source_dir.iterdir()}
        resolved: list[str] = []
        missing: list[str] = []
        for selected in self._selected_item_names:
            match = names_by_lower.get(selected.lower())
            if match is None:
                missing.append(selected)
            else:
                resolved.append(match)
        if missing:
            messagebox.showwarning(
                "Selection Updated",
                f"Some selected items were not found and will be skipped:\n- " + "\n- ".join(missing[:8]),
            )
        self._selected_item_names = resolved if resolved else []
        self._update_selected_items_label()
        return resolved

    def _build_case_panel(self, parent):
        inner = self._card(parent, "Case Metadata (Optional)")
        self._use_metadata_var = tk.BooleanVar(value=bool(self._settings.get("use_metadata", False)))
        metadata_cb = tk.Checkbutton(inner, text="Enable forensic reporting metadata", variable=self._use_metadata_var, command=self._on_metadata_toggle, bg=theme.BG2, fg=theme.ACCENT, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=("Segoe UI", 10, "bold"), cursor="hand2")
        metadata_cb.pack(anchor="w", pady=(0, 8))
        self._register_mutable(metadata_cb)
        self._meta_fields_frame = tk.Frame(inner, bg=theme.BG2)
        self._meta_fields_frame.pack(fill="x")
        self._meta_vars = {
            "Examiner": tk.StringVar(value=str(self._settings.get("metadata_examiner", ""))),
            "Case ID": tk.StringVar(value=str(self._settings.get("metadata_case_id", ""))),
            "Evidence ID": tk.StringVar(value=str(self._settings.get("metadata_evidence_id", ""))),
            "Notes": tk.StringVar(value=str(self._settings.get("metadata_notes", ""))),
        }
        for label, var in self._meta_vars.items():
            self._labeled_entry(self._meta_fields_frame, f"{label}:", var)
        tk.Label(inner, text="Advanced: optional metadata appears in manifest and session reports.", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))
        self._on_metadata_toggle()

    def _on_metadata_toggle(self):
        state = "normal" if self._use_metadata_var.get() else "disabled"
        for child in self._meta_fields_frame.winfo_children():
            for widget in child.winfo_children():
                if isinstance(widget, (tk.Entry, tk.Label)):
                    try:
                        widget.config(state=state)
                    except tk.TclError:
                        pass

    def _build_archive_panel(self, parent):
        inner = self._card(parent, "Basic Archive Setup")
        row1 = tk.Frame(inner, bg=theme.BG2)
        row1.pack(fill="x", pady=3)
        tk.Label(row1, text="Format:", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._fmt_var = tk.StringVar(value=str(self._settings.get("archive_fmt", ARCHIVE_FORMATS[0])))
        format_cb = ttk.Combobox(row1, textvariable=self._fmt_var, values=ARCHIVE_FORMATS, state="readonly", width=14)
        format_cb.pack(side="left")
        format_cb.bind("<<ComboboxSelected>>", self._on_format_changed)
        self._register_mutable(format_cb, enabled_state="readonly", disabled_state="disabled")

        row2 = tk.Frame(inner, bg=theme.BG2)
        row2.pack(fill="x", pady=3)
        tk.Label(row2, text="Compression:", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._level_var = tk.StringVar(value=str(self._settings.get("compress_level_label", "Normal (5)")))
        level_cb = ttk.Combobox(row2, textvariable=self._level_var, values=list(COMPRESSION_LEVELS.keys()), state="readonly", width=22)
        level_cb.pack(side="left")
        self._register_mutable(level_cb, enabled_state="readonly", disabled_state="disabled")

        tk.Label(inner, text="Basic step 2: choose archive format and compression. Advanced behavior is below.", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(4, 8))

        controls_row = tk.Frame(inner, bg=theme.BG2)
        controls_row.pack(fill="x", pady=(4, 0))
        self._start_btn = tk.Button(controls_row, text="Start Processing", command=self._start, bg=theme.ACCENT, fg=theme.BG, font=FONT_HEAD, relief="flat", activebackground="#79c0ff", activeforeground=theme.BG, cursor="hand2", padx=20, pady=8)
        self._start_btn.pack(side="left", padx=(0, 10))
        self._cancel_btn = tk.Button(controls_row, text="Cancel All", command=self._cancel, bg=theme.BG3, fg=theme.RED, font=FONT_HEAD, relief="flat", activebackground=theme.RED, activeforeground=theme.WHITE, cursor="hand2", padx=14, pady=8, state="disabled")
        self._cancel_btn.pack(side="left")
        tk.Label(inner, text="Before run, a session summary appears. Destructive mode requires typing DELETE.", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))

    def _build_advanced_options_panel(self, parent):
        inner = self._card(parent, "Advanced Runtime Policies")
        split_row = tk.Frame(inner, bg=theme.BG2)
        split_row.pack(fill="x", pady=3)
        self._split_enabled = tk.BooleanVar(value=bool(self._settings.get("split_enabled", False)))
        split_cb = tk.Checkbutton(split_row, text="Split Archive (7z only):", variable=self._split_enabled, command=self._on_split_toggle, bg=theme.BG2, fg=theme.FG, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=FONT_MAIN, cursor="hand2")
        split_cb.pack(side="left")
        self._register_mutable(split_cb)
        self._split_size_var = tk.StringVar(value=str(self._settings.get("split_size_str", "4")))
        self._split_entry = tk.Entry(split_row, textvariable=self._split_size_var, bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, relief="flat", font=FONT_MAIN, width=6, highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT, state="disabled", disabledbackground=theme.BG3, disabledforeground=theme.FG2)
        self._split_entry.pack(side="left", padx=(6, 0))
        self._register_mutable(self._split_entry)
        tk.Label(split_row, text="GB", fg=theme.FG2, bg=theme.BG2, font=FONT_MAIN).pack(side="left", padx=4)
        self._help_btn(split_row, "Split Archive Policy", "Automatically segments large evidence collections into fixed-size volumes (e.g., 4GB for FAT32 compatibility or DVD storage).")

        row3 = tk.Frame(inner, bg=theme.BG2)
        row3.pack(fill="x", pady=3)
        tk.Label(row3, text="Concurrent Jobs:", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._threads_var = tk.IntVar(value=int(self._settings.get("threads", 4)))
        threads_spin = tk.Spinbox(row3, from_=1, to=16, textvariable=self._threads_var, bg=theme.BG3, fg=theme.WHITE, width=5, relief="flat", font=FONT_MAIN, buttonbackground=theme.BG3)
        threads_spin.pack(side="left")
        self._register_mutable(threads_spin)
        self._auto_threads_var = tk.BooleanVar(value=bool(self._settings.get("auto_threads", False)))
        auto_threads_cb = tk.Checkbutton(row3, text="Auto", variable=self._auto_threads_var, bg=theme.BG2, fg=theme.FG2, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=("Segoe UI", 9), cursor="hand2")
        auto_threads_cb.pack(side="left", padx=(8, 0))
        self._register_mutable(auto_threads_cb)
        self._help_btn(row3, "Concurrency Policy", "'Fixed' processes specified cases simultaneously. 'Auto' intelligently throttles based on file sizes to prevent I/O bottlenecks and disk thrashing.")

        hash_threads_row = tk.Frame(inner, bg=theme.BG2)
        hash_threads_row.pack(fill="x", pady=3)
        tk.Label(hash_threads_row, text="Hash Threads:", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._hash_threads_var = tk.IntVar(value=int(self._settings.get("hash_threads", 4)))
        hash_threads_spin = tk.Spinbox(hash_threads_row, from_=1, to=32, textvariable=self._hash_threads_var, bg=theme.BG3, fg=theme.WHITE, width=5, relief="flat", font=FONT_MAIN, buttonbackground=theme.BG3)
        hash_threads_spin.pack(side="left")
        self._register_mutable(hash_threads_spin)
        tk.Label(hash_threads_row, text="threads used for parallel file hashing inside each job", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))
        self._help_btn(hash_threads_row, "Hashing Parallelism", "Controls internal parallelism of the manifest engine. Allows the tool to read and hash multiple files in parallel before writing the manifest.")

        pw_row = tk.Frame(inner, bg=theme.BG2)
        pw_row.pack(fill="x", pady=3)
        tk.Label(pw_row, text="Password (7z only):", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._pw_var = tk.StringVar()
        pw_entry = tk.Entry(pw_row, textvariable=self._pw_var, show="*", bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, relief="flat", font=FONT_MAIN, highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        pw_entry.pack(side="left", fill="x", expand=True)
        self._register_mutable(pw_entry)
        self._show_pw = tk.BooleanVar(value=False)
        show_pw_cb = tk.Checkbutton(pw_row, text="Show", variable=self._show_pw, command=lambda: pw_entry.config(show="" if self._show_pw.get() else "*"), bg=theme.BG2, fg=theme.FG2, activebackground=theme.BG2, selectcolor=theme.BG3, font=("Segoe UI", 9), cursor="hand2")
        show_pw_cb.pack(side="left", padx=6)
        self._register_mutable(show_pw_cb)

        flags_row = tk.Frame(inner, bg=theme.BG2)
        flags_row.pack(fill="x", pady=(6, 3))
        self._resume_var = tk.BooleanVar(value=bool(self._settings.get("resume_enabled", False)))
        self._dry_run_var = tk.BooleanVar(value=bool(self._settings.get("dry_run", False)))
        self._fast_scan_var = tk.BooleanVar(value=str(self._settings.get("scan_mode", "deterministic")) == "fast")
        self._skip_archive_hash_var = tk.BooleanVar(value=str(self._settings.get("archive_hash_mode", "always")) == "skip")
        self._report_json_var = tk.BooleanVar(value=bool(self._settings.get("report_json", False)))
        self._embed_manifest_in_archive_var = tk.BooleanVar(value=bool(self._settings.get("embed_manifest_in_archive", True)))
        for text, var in [
            ("Resume", self._resume_var),
            ("Dry Run", self._dry_run_var),
            ("Fast Scan", self._fast_scan_var),
            ("Skip Archive Hash", self._skip_archive_hash_var),
            ("JSON Report", self._report_json_var),
            ("Embed Manifest", self._embed_manifest_in_archive_var),
        ]:
            cb = tk.Checkbutton(flags_row, text=text, variable=var, bg=theme.BG2, fg=theme.FG, activebackground=theme.BG2, activeforeground=theme.WHITE, selectcolor=theme.BG3, font=FONT_MAIN, cursor="hand2")
            cb.pack(side="left", padx=(0, 8))
            if text == "Skip Archive Hash":
                self._skip_archive_hash_cb = cb
            self._register_mutable(cb)
            
            # Map help text for flags
            help_data = {
                "Resume": ("Session Resume", "Uses a local state database to track progress. Re-uses already verified/hashed items even if settings change."),
                "Dry Run": ("Simulation Mode", "Executes the entire scan and manifest planning without writing any data. Use for vetting paths and disk requirements."),
                "Fast Scan": ("Optimized Scanning", "Snapshot-based discovery optimized for extremely high-file-count disks where directory traversal is a bottleneck."),
                "Skip Archive Hash": ("Phase Skipping", "Skips the final full-container hash to save time on multi-terabyte archives while maintaining file-level manifest hashes."),
                "JSON Report": ("JSON Output", "Writes a machine-readable JSON report in addition to TXT and CSV reports."),
                "Embed Manifest": ("Manifest In Archive", "Adds the generated manifest report into each output archive. Disable if you want archive contents to contain only source evidence files."),
            }
            if text in help_data:
                h_title, h_msg = help_data[text]
                self._help_btn(flags_row, h_title, h_msg)

        adv_row = tk.Frame(inner, bg=theme.BG2)
        adv_row.pack(fill="x", pady=3)
        tk.Label(adv_row, text="Progress Interval (ms):", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._progress_interval_var = tk.IntVar(value=int(self._settings.get("progress_interval_ms", 200)))
        interval_spin = tk.Spinbox(adv_row, from_=0, to=5000, textvariable=self._progress_interval_var, bg=theme.BG3, fg=theme.WHITE, width=7, relief="flat", font=FONT_MAIN, buttonbackground=theme.BG3)
        interval_spin.pack(side="left")
        self._register_mutable(interval_spin)
        self._help_btn(adv_row, "Progress Interval", "Throttles progress event updates to reduce UI overhead. Lower values update more frequently, higher values reduce noise.")

        db_row = tk.Frame(inner, bg=theme.BG2)
        db_row.pack(fill="x", pady=3)
        tk.Label(db_row, text="State DB (optional):", width=18, anchor="w", fg=theme.FG, bg=theme.BG2, font=FONT_MAIN).pack(side="left")
        self._state_db_var = tk.StringVar(value=str(self._settings.get("state_db_path", "")))
        state_db_entry = tk.Entry(db_row, textvariable=self._state_db_var, bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, relief="flat", font=FONT_MAIN, highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT)
        state_db_entry.pack(side="left", fill="x", expand=True)
        self._register_mutable(state_db_entry)
        self._clear_state_db_btn(db_row)
        self._browse_state_db_btn(db_row)
        self._help_btn(db_row, "State Database Path", "Choose where the SQLite resume database is stored. Leave default to place it in the output folder.")
        tk.Label(inner, text="Advanced policies can speed large runs but may change processing behavior.", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))
        self._refresh_archive_hash_option_state()
        self._on_split_toggle()

    def _on_split_toggle(self):
        if self._fmt_var.get() != "7z":
            self._split_entry.config(state="disabled")
            return
        self._split_entry.config(state="normal" if self._split_enabled.get() else "disabled")

    def _on_format_changed(self, _event=None):
        if self._fmt_var.get() != "7z":
            self._split_entry.config(state="disabled")
        else:
            self._on_split_toggle()

    def _build_hash_panel(self, parent):
        inner = self._card(parent, "Hash Algorithms")
        self._hash_vars = {}
        selected_hashes = {str(value) for value in self._settings.get("hash_algorithms", ["SHA256"])}
        row = tk.Frame(inner, bg=theme.BG2)
        row.pack(fill="x")
        for alg in HASH_NAMES:
            var = tk.BooleanVar(value=(alg in selected_hashes))
            self._hash_vars[alg] = var
            cb = tk.Checkbutton(
                row,
                text=alg,
                variable=var,
                command=self._on_hash_selection_changed,
                bg=theme.BG2,
                fg=theme.FG,
                activebackground=theme.BG2,
                activeforeground=theme.WHITE,
                selectcolor=theme.BG3,
                font=FONT_MAIN,
                cursor="hand2",
                padx=8,
            )
            cb.pack(side="left")
            self._register_mutable(cb)
        tk.Label(inner, text="Basic step 3: hashing is optional; leave all unchecked to skip file/archive hashing.", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", pady=(4, 0))
        self._refresh_archive_hash_option_state()

    def _selected_hash_algorithms(self) -> list[str]:
        return [alg for alg, var in self._hash_vars.items() if var.get()]

    def _refresh_archive_hash_option_state(self) -> None:
        if not hasattr(self, "_skip_archive_hash_var"):
            return
        has_hashes = bool(self._selected_hash_algorithms()) if hasattr(self, "_hash_vars") else False
        if not has_hashes:
            self._skip_archive_hash_var.set(False)
        if self._skip_archive_hash_cb is not None:
            state = "normal" if has_hashes else "disabled"
            self._skip_archive_hash_cb.config(state=state)

    def _on_hash_selection_changed(self) -> None:
        self._refresh_archive_hash_option_state()

    def _build_queue_panel(self, parent):
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.pack(fill="x", pady=(0, 10))
        outer.pack_propagate(False)
        outer.config(height=240)
        header = tk.Frame(outer, bg=theme.BG2)
        header.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(header, text="Compression Queue", font=FONT_HEAD, fg=theme.ACCENT, bg=theme.BG2).pack(side="left")
        self._queue_count_lbl = tk.Label(header, text="0 item(s)", font=("Segoe UI", 9), fg=theme.FG2, bg=theme.BG2)
        self._queue_count_lbl.pack(side="right")
        filters = tk.Frame(outer, bg=theme.BG2)
        filters.pack(fill="x", padx=14, pady=(4, 0))
        for name in ["All", "Running", "Done", "Failed", "Skipped"]:
            btn = tk.Button(
                filters,
                text=f"{name} (0)",
                command=lambda selected=name: self._set_queue_filter(selected),
                bg=theme.BG3 if name != "All" else theme.ACCENT,
                fg=theme.FG if name != "All" else theme.BG,
                font=("Segoe UI", 8),
                relief="flat",
                activebackground=theme.ACCENT,
                activeforeground=theme.BG,
                cursor="hand2",
                padx=8,
                pady=3,
            )
            btn.pack(side="left", padx=(0, 6))
            self._queue_filter_buttons[name] = btn
        tk.Label(filters, text="Sort:", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 8)).pack(side="left", padx=(8, 4))
        self._queue_sort_var = tk.StringVar(value="Name")
        sort_cb = ttk.Combobox(filters, textvariable=self._queue_sort_var, values=["Name", "Status", "Phase"], state="readonly", width=8)
        sort_cb.pack(side="left", padx=(0, 4))
        sort_cb.bind("<<ComboboxSelected>>", lambda _e: self._apply_queue_filter())
        self._queue_sort_order_var = tk.StringVar(value="Asc")
        order_cb = ttk.Combobox(filters, textvariable=self._queue_sort_order_var, values=["Asc", "Desc"], state="readonly", width=6)
        order_cb.pack(side="left")
        order_cb.bind("<<ComboboxSelected>>", lambda _e: self._apply_queue_filter())
        tk.Frame(outer, bg=theme.BORDER, height=1).pack(fill="x", padx=14, pady=(4, 6))
        canvas_frame = tk.Frame(outer, bg=theme.BG2)
        canvas_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self._queue_canvas = tk.Canvas(canvas_frame, bg=theme.BG2, highlightthickness=0, height=180)
        y_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._queue_canvas.yview)
        x_scroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self._queue_canvas.xview)
        self._queue_canvas.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")
        self._queue_canvas.pack(side="left", fill="both", expand=True)
        self._queue_inner = tk.Frame(self._queue_canvas, bg=theme.BG2)
        self._queue_canvas_window = self._queue_canvas.create_window((0, 0), window=self._queue_inner, anchor="nw")
        self._queue_inner.bind("<Configure>", lambda _e: self._queue_canvas.configure(scrollregion=self._queue_canvas.bbox("all")))
        self._queue_placeholder = tk.Label(self._queue_inner, text="Queue will populate when processing starts ...", fg=theme.FG2, bg=theme.BG2, font=("Segoe UI", 9))
        self._queue_placeholder.pack(pady=20)

    def _build_queue_rows(self, item_names: list[str]):
        for widget in self._queue_inner.winfo_children():
            widget.destroy()
        self._queue_rows.clear()
        self._queue_count_lbl.config(text=f"{len(item_names)} item(s)")
        for idx, name in enumerate(item_names):
            row_bg = theme.BG2 if idx % 2 == 0 else theme.BG3
            row = tk.Frame(self._queue_inner, bg=row_bg)
            status_lbl = tk.Label(row, text="Queued", width=9, fg=theme.FG2, bg=row_bg, font=("Segoe UI", 9, "bold"))
            status_lbl.pack(side="left", padx=(6, 4), pady=4)
            name_display = name if len(name) <= 40 else name[:37] + "..."
            name_lbl = tk.Label(row, text=name_display, fg=theme.FG, bg=row_bg, font=FONT_MAIN, anchor="w", width=40)
            name_lbl.pack(side="left", padx=(0, 8))
            phase_lbl = tk.Label(row, text="queued", fg=theme.FG2, bg=row_bg, font=("Segoe UI", 9), width=16, anchor="w")
            phase_lbl.pack(side="left", padx=(0, 8))
            pb = ttk.Progressbar(row, orient="horizontal", mode="determinate", length=200, style="Horizontal.TProgressbar")
            pb.pack(side="left", padx=(0, 8), pady=4)
            skip_btn = tk.Button(
                row,
                text="Skip",
                command=lambda job_id=idx: self._request_skip_job(job_id),
                bg=theme.BG3,
                fg=theme.YELLOW,
                font=("Segoe UI", 8, "bold"),
                relief="flat",
                activebackground=theme.YELLOW,
                activeforeground=theme.BG,
                cursor="hand2",
                padx=8,
                pady=3,
                state="disabled",
            )
            skip_btn.pack(side="right", padx=(0, 6))
            why_btn = tk.Button(
                row,
                text="Why",
                command=lambda job_id=idx: self._show_failure_reason(job_id),
                bg=theme.BG3,
                fg=theme.RED,
                font=("Segoe UI", 8, "bold"),
                relief="flat",
                activebackground=theme.RED,
                activeforeground=theme.WHITE,
                cursor="hand2",
                padx=8,
                pady=3,
                state="disabled",
            )
            why_btn.pack(side="right", padx=(0, 6))
            why_btn.pack_forget()
            self._queue_rows.append(
                {
                    "index": idx,
                    "name": name,
                    "frame": row,
                    "status_lbl": status_lbl,
                    "phase_lbl": phase_lbl,
                    "progress": pb,
                    "skip_btn": skip_btn,
                    "why_btn": why_btn,
                    "why_visible": False,
                    "failure_reason": "",
                    "state": "queued",
                }
            )
            self._sync_skip_button_state(self._queue_rows[-1])
            self._sync_why_button_state(self._queue_rows[-1])
        self._apply_queue_filter()
        self._refresh_queue_filter_counts()

    def _sync_skip_button_state(self, row: dict[str, object]) -> None:
        skip_btn = row.get("skip_btn")
        if not isinstance(skip_btn, tk.Button):
            return
        state = str(row.get("state", ""))
        can_skip = (
            self._token is not None
            and self._thread is not None
            and self._thread.is_alive()
            and state in {"queued", "running"}
        )
        skip_btn.config(state="normal" if can_skip else "disabled")

    def _disable_all_skip_buttons(self) -> None:
        for row in self._queue_rows:
            skip_btn = row.get("skip_btn")
            if isinstance(skip_btn, tk.Button):
                skip_btn.config(state="disabled")

    def _sync_why_button_state(self, row: dict[str, object]) -> None:
        why_btn = row.get("why_btn")
        if not isinstance(why_btn, tk.Button):
            return
        state = str(row.get("state", ""))
        should_show = state in {"error", "warning", "skipped", "cancelled"}
        is_visible = bool(row.get("why_visible", False))
        if should_show and not is_visible:
            why_btn.pack(side="right", padx=(0, 6))
            row["why_visible"] = True
        elif not should_show and is_visible:
            why_btn.pack_forget()
            row["why_visible"] = False
        if should_show:
            reason = str(row.get("failure_reason", "")).strip()
            why_btn.config(state="normal" if reason else "disabled")
        else:
            why_btn.config(state="disabled")

    def _show_failure_reason(self, job_id: int) -> None:
        if job_id >= len(self._queue_rows):
            return
        row = self._queue_rows[job_id]
        name = str(row.get("name", f"Job {job_id}"))
        reason = str(row.get("failure_reason", "")).strip() or "No failure details were captured."
        messagebox.showerror(f"Failure Detail - {name}", reason)

    def _request_skip_job(self, job_id: int) -> None:
        if self._token is None or job_id >= len(self._queue_rows):
            return
        row = self._queue_rows[job_id]
        state = str(row.get("state", ""))
        if state not in {"queued", "running"}:
            return
        self._token.request_skip(job_id)
        row["failure_reason"] = "Job skipped by operator request."
        phase_lbl = row.get("phase_lbl")
        if isinstance(phase_lbl, tk.Label):
            phase_lbl.config(text="skip requested", fg=theme.YELLOW)
        skip_btn = row.get("skip_btn")
        if isinstance(skip_btn, tk.Button):
            skip_btn.config(text="Pending", state="disabled")
        self._ui_queue.put(("log", (f"[{self._time()}] [!] Skip requested for job {job_id}", theme.YELLOW)))

    def _set_queue_filter(self, selected: str):
        self._queue_filter = selected
        self._apply_queue_filter()

    def _queue_sort_key(self, row: dict[str, object]) -> tuple[object, ...]:
        mode = self._queue_sort_var.get()
        if mode == "Status":
            return natural_text_key(str(row.get("state", "")))
        if mode == "Phase":
            phase_lbl = row.get("phase_lbl")
            phase_text = phase_lbl.cget("text") if isinstance(phase_lbl, tk.Label) else ""
            return natural_text_key(str(phase_text))
        return natural_text_key(str(row.get("name", "")))

    def _apply_queue_filter(self):
        for row in self._queue_rows:
            row["frame"].pack_forget()
        filtered_rows = [row for row in self._queue_rows if matches_queue_filter(str(row["state"]), self._queue_filter)]
        reverse = self._queue_sort_order_var.get() == "Desc"
        for row in sorted(filtered_rows, key=self._queue_sort_key, reverse=reverse):
            if matches_queue_filter(str(row["state"]), self._queue_filter):
                row["frame"].pack(fill="x", pady=1, anchor="w")
        for name, btn in self._queue_filter_buttons.items():
            is_active = name == self._queue_filter
            btn.config(bg=theme.ACCENT if is_active else theme.BG3, fg=theme.BG if is_active else theme.FG)

    def _refresh_queue_filter_counts(self):
        counts = queue_filter_counts([str(row["state"]) for row in self._queue_rows])
        for name, btn in self._queue_filter_buttons.items():
            btn.config(text=f"{name} ({counts.get(name, 0)})")

    def _build_controls(self, parent):
        ctrl = tk.Frame(parent, bg=parent.cget("bg"))
        ctrl.pack(fill="x", pady=6)
        quick = tk.Frame(ctrl, bg=parent.cget("bg"))
        quick.pack(side="left")
        self._copy_report_btn = tk.Button(quick, text="Copy Last Report Path", command=self._copy_last_report_path, bg=theme.BG3, fg=theme.ACCENT, font=("Segoe UI", 9), relief="flat", activebackground=theme.BORDER, activeforeground=theme.FG, cursor="hand2", padx=10, pady=8)
        self._copy_report_btn.pack(side="left", padx=(0, 8))
        self._open_output_btn = tk.Button(quick, text="Open Output Folder", command=self._open_output_folder, bg=theme.BG3, fg=theme.ACCENT, font=("Segoe UI", 9), relief="flat", activebackground=theme.BORDER, activeforeground=theme.FG, cursor="hand2", padx=10, pady=8)
        self._open_output_btn.pack(side="left", padx=(0, 8))
        self._verbose_btn = tk.Button(quick, text="Verbose Console", command=self._open_verbose, bg=theme.BG3, fg=theme.ACCENT, font=("Segoe UI", 9), relief="flat", activebackground=theme.BORDER, activeforeground=theme.FG, cursor="hand2", padx=10, pady=8)
        self._verbose_btn.pack(side="left", padx=(0, 8))
        self._diag_btn = tk.Button(quick, text="Run Diagnostics", command=self._run_diagnostics, bg=theme.BG3, fg=theme.ACCENT, font=("Segoe UI", 9), relief="flat", activebackground=theme.BORDER, activeforeground=theme.FG, cursor="hand2", padx=10, pady=8)
        self._diag_btn.pack(side="left", padx=(0, 8))
        self._uac_btn = tk.Button(quick, text="Relaunch as Admin", command=self._manual_uac_relaunch, bg=theme.BG3, fg=theme.YELLOW, font=("Segoe UI", 9), relief="flat", activebackground=theme.BORDER, activeforeground=theme.WHITE, cursor="hand2", padx=10, pady=8)
        self._uac_btn.pack(side="left", padx=(0, 8))
        self._clear_btn = tk.Button(quick, text="Clear Log", command=self._clear_log, bg=theme.BG3, fg=theme.FG2, font=("Segoe UI", 9), relief="flat", activebackground=theme.BORDER, activeforeground=theme.FG, cursor="hand2", padx=10, pady=8)
        self._clear_btn.pack(side="left")
        self._help_btn(quick, "Quick Actions", "Copy report path, open output folder, inspect verbose logs, run diagnostics, relaunch as admin, and clear the live log.")

        stats = tk.Frame(ctrl, bg=parent.cget("bg"))
        stats.pack(side="right")
        self._status_var = tk.StringVar(value="Ready")
        self._stats_var = tk.StringVar(value="Processed 0/0 | Phase: idle | Elapsed: 00:00 | ETA: --:--")
        tk.Label(stats, textvariable=self._stats_var, fg=theme.FG2, bg=theme.BG, font=("Segoe UI", 9)).pack(side="right")
        tk.Label(stats, textvariable=self._status_var, fg=theme.FG2, bg=theme.BG, font=("Segoe UI", 9, "bold")).pack(side="right", padx=(0, 12))

    def _build_log_panel(self, parent):
        outer = ttk.Frame(parent, style="Card.TFrame")
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="Live Log", font=FONT_HEAD, fg=theme.ACCENT, bg=theme.BG2).pack(anchor="w", padx=14, pady=(10, 4))
        tk.Frame(outer, bg=theme.BORDER, height=1).pack(fill="x", padx=14, pady=(0, 6))
        prg_frame = tk.Frame(outer, bg=theme.BG2)
        prg_frame.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(prg_frame, text="Overall:", fg=theme.FG2, bg=theme.BG2, font=FONT_MAIN, width=8, anchor="w").grid(row=0, column=0, sticky="w")
        self._prog_overall = ttk.Progressbar(prg_frame, orient="horizontal", mode="determinate", length=300)
        self._prog_overall.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=2)
        prg_frame.columnconfigure(1, weight=1)
        log_frame = tk.Frame(outer, bg=theme.BG2)
        log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self._log = tk.Text(log_frame, bg=theme.BG, fg=theme.FG, font=FONT_MONO, wrap="word", relief="flat", highlightthickness=1, highlightbackground=theme.BORDER, state="disabled", cursor="arrow")
        scrollbar = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        for tag, colour in [("info", theme.FG), ("success", theme.GREEN), ("warn", theme.YELLOW), ("error", theme.RED), ("accent", theme.ACCENT), ("white", theme.WHITE), ("muted", theme.FG2), ("purple", theme.PURPLE)]:
            self._log.tag_configure(tag, foreground=colour)

    def _log_write(self, message: str, colour: str = theme.FG):
        colour_map = {theme.FG: "info", theme.GREEN: "success", theme.YELLOW: "warn", theme.RED: "error", theme.ACCENT: "accent", theme.WHITE: "white", theme.FG2: "muted", theme.PURPLE: "purple"}
        self._log.configure(state="normal")
        self._log.insert("end", message + "\n", colour_map.get(colour, "info"))
        self._log.configure(state="disabled")
        self._log.see("end")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _copy_last_report_path(self):
        if not self._last_report_path:
            messagebox.showinfo("No Report", "No report has been generated yet.")
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_report_path)
        self._status_var.set("Last report path copied.")

    def _open_output_folder(self):
        target = self._dst_var.get().strip() or self._last_output_dir
        if not target:
            messagebox.showinfo("No Output Folder", "No output folder is available yet.")
            return
        path = Path(target)
        if not path.exists():
            messagebox.showerror("Folder Missing", f"Output folder not found:\n{path}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            messagebox.showerror("Open Failed", f"Could not open folder:\n{exc}")

    def _run_diagnostics(self):
        def _write_probe(path: Path) -> str:
            try:
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ".forensicpack_write_probe.tmp"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return "OK"
            except Exception as exc:
                return f"FAIL ({exc})"

        source_text = self._src_var.get().strip()
        output_text = self._dst_var.get().strip()
        source_path = Path(source_text) if source_text else None
        output_path = Path(output_text) if output_text else None
        tk_patch = str(self.tk.call("info", "patchlevel"))
        seven_zip = find_7zip() or "NOT FOUND"
        source_check = "N/A"
        if source_path is not None:
            source_check = "OK" if source_path.exists() and source_path.is_dir() else "FAIL (missing or not a directory)"
        output_check = "N/A" if output_path is None else _write_probe(output_path)
        lines = [
            "ForensicPack Diagnostics",
            "-" * 30,
            f"Python       : {sys.version.split()[0]}",
            f"Python Path  : {sys.executable}",
            f"Tk Version   : {tk_patch}",
            f"7-Zip Path   : {seven_zip}",
            f"Source Check : {source_check}",
            f"Output Check : {output_check}",
            f"Settings File: {settings_path()}",
        ]
        report = "\n".join(lines)
        self._ui_queue.put(("log", (f"[{self._time()}] [DIAG] Python={sys.version.split()[0]} Tk={tk_patch} 7z={seven_zip}", theme.FG2)))
        self._ui_queue.put(("log", (f"[{self._time()}] [DIAG] Source={source_check} Output={output_check}", theme.FG2)))
        messagebox.showinfo("Diagnostics", report)

    def _update_runtime_stats(self):
        total = len(self._queue_rows)
        completed = sum(1 for row in self._queue_rows if str(row["state"]) in {"done", "warning", "error", "skipped", "cancelled"})
        elapsed = self._last_elapsed_seconds
        if self._run_started_at is not None:
            elapsed = max(0.0, time.monotonic() - self._run_started_at)
            self._last_elapsed_seconds = elapsed
        eta = estimate_eta_seconds(elapsed, completed, total)
        eta_finish_label = "--:--"
        if eta is not None:
            import datetime as dt

            finish_time = dt.datetime.now() + dt.timedelta(seconds=eta)
            eta_finish_label = finish_time.strftime("%H:%M:%S")
        self._stats_var.set(
            f"Processed {completed}/{total} | Phase: {self._current_phase} | Elapsed: {format_duration(elapsed)} | ETA: {format_duration(eta)} | Finish: {eta_finish_label}"
        )

    def _open_verbose(self):
        if hasattr(self, "_verbose_win") and self._verbose_win.winfo_exists():
            self._verbose_win.lift()
            return
        self._verbose_win = tk.Toplevel(self)
        self._verbose_win.title("Verbose Console")
        self._verbose_win.geometry("800x600")
        self._verbose_win.configure(bg=theme.BG)
        self._verbose_text = tk.Text(self._verbose_win, bg=theme.BG, fg=theme.FG2, font=FONT_MONO, wrap="word", relief="flat", highlightthickness=0, state="disabled")
        scrollbar = ttk.Scrollbar(self._verbose_win, command=self._verbose_text.yview)
        self._verbose_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._verbose_text.pack(side="left", fill="both", expand=True, padx=10, pady=10)

    def _drain_ui_queue(self):
        while True:
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log_write(*payload)
            elif kind == "status":
                self._status_var.set(payload)
            elif kind == "overall":
                self._prog_overall.configure(value=payload * 100)
            elif kind == "queue":
                self._build_queue_rows(payload)
            elif kind == "item_status":
                self._update_item_status(*payload)
            elif kind == "item_progress":
                self._update_item_progress(*payload)
            elif kind == "item_failure":
                self._update_item_failure(*payload)
            elif kind == "verbose" and hasattr(self, "_verbose_text") and self._verbose_win.winfo_exists():
                self._verbose_text.configure(state="normal")
                self._verbose_text.insert("end", payload + "\n")
                self._verbose_text.configure(state="disabled")
                self._verbose_text.see("end")
            elif kind == "permission_error":
                self._prompt_uac_relaunch(payload)
            elif kind == "done":
                self._done()
        self._update_runtime_stats()
        self.after(50, self._drain_ui_queue)

    def _relaunch_as_admin(self) -> bool:
        if not sys.platform.startswith("win"):
            messagebox.showerror("Elevation Not Supported", "Administrator relaunch is only supported on Windows.")
            return False
        try:
            import ctypes

            executable, parameters = build_windows_elevation_command(__file__)
            rc = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                executable,
                parameters,
                None,
                1,
            )
            if rc <= 32:
                raise OSError(f"ShellExecuteW failed with code {rc}")
            return True
        except Exception as exc:
            messagebox.showerror("Elevation Failed", f"Could not relaunch as Administrator:\n{exc}")
            return False

    def _prompt_uac_relaunch(self, detail: str) -> None:
        if not sys.platform.startswith("win"):
            messagebox.showerror("Permission Error", detail)
            return
        message = (
            "A permission error occurred while accessing files.\n\n"
            f"{detail}\n\n"
            "Relaunch ForensicPack as Administrator?"
        )
        if not messagebox.askyesno("Permission Denied", message):
            return
        if self._relaunch_as_admin():
            self._status_var.set("Relaunching as Administrator ...")
            self.after(150, self.destroy)

    def _manual_uac_relaunch(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            proceed = messagebox.askyesno(
                "Session Running",
                "A session is currently running. Relaunching as Administrator will close this window.\n\nContinue?",
            )
            if not proceed:
                return
        if self._relaunch_as_admin():
            self._status_var.set("Relaunching as Administrator ...")
            self.after(150, self.destroy)

    def _update_item_status(self, idx: int, state: str):
        if idx >= len(self._queue_rows):
            return
        row = self._queue_rows[idx]
        status_lbl = row["status_lbl"]
        phase_lbl = row["phase_lbl"]
        pb = row["progress"]
        row["state"] = state
        if state == "running":
            self._running_jobs.add(idx)
            status_lbl.config(text="RUNNING", fg=theme.ACCENT)
            phase_lbl.config(text="running", fg=theme.ACCENT)
            row["failure_reason"] = ""
        elif state == "done":
            self._running_jobs.discard(idx)
            status_lbl.config(text="DONE", fg=theme.GREEN)
            phase_lbl.config(text="done", fg=theme.GREEN)
            pb.config(style="Green.Horizontal.TProgressbar", value=100)
            row["failure_reason"] = ""
        elif state == "warning":
            self._running_jobs.discard(idx)
            status_lbl.config(text="WARN", fg=theme.YELLOW)
            phase_lbl.config(text="completed with warning", fg=theme.YELLOW)
            pb.config(style="Yellow.Horizontal.TProgressbar", value=100)
        elif state == "error":
            self._running_jobs.discard(idx)
            status_lbl.config(text="FAILED", fg=theme.RED)
            phase_lbl.config(text="failed", fg=theme.RED)
            pb.config(style="Red.Horizontal.TProgressbar")
        elif state == "skipped":
            self._running_jobs.discard(idx)
            status_lbl.config(text="SKIPPED", fg=theme.YELLOW)
            phase_lbl.config(text="skipped", fg=theme.YELLOW)
            pb.config(style="Yellow.Horizontal.TProgressbar", value=100)
            if not str(row.get("failure_reason", "")).strip():
                row["failure_reason"] = "Job skipped by operator or policy."
        elif state == "cancelled":
            self._running_jobs.discard(idx)
            status_lbl.config(text="CANCEL", fg=theme.YELLOW)
            phase_lbl.config(text="cancelled", fg=theme.YELLOW)
            pb.config(style="Yellow.Horizontal.TProgressbar")
            if not str(row.get("failure_reason", "")).strip():
                row["failure_reason"] = "Job cancelled by operator."
        if state in {"done", "warning", "error", "skipped", "cancelled"}:
            skip_btn = row.get("skip_btn")
            if isinstance(skip_btn, tk.Button):
                skip_btn.config(text="Skip")
        self._sync_why_button_state(row)
        self._sync_skip_button_state(row)
        self._refresh_queue_filter_counts()
        self._apply_queue_filter()

    def _update_item_failure(self, idx: int, reason: str):
        if idx >= len(self._queue_rows):
            return
        row = self._queue_rows[idx]
        row["failure_reason"] = reason
        phase_lbl = row.get("phase_lbl")
        if isinstance(phase_lbl, tk.Label):
            state = str(row.get("state", ""))
            if state == "warning":
                phase_lbl.config(text="warning (see Why)", fg=theme.YELLOW)
            elif state == "error":
                phase_lbl.config(text="failed (see Why)", fg=theme.RED)
        self._sync_why_button_state(row)

    def _update_item_progress(self, idx: int, fraction: float, phase: str):
        if idx >= len(self._queue_rows):
            return
        row = self._queue_rows[idx]
        status_lbl = row["status_lbl"]
        phase_lbl = row["phase_lbl"]
        pb = row["progress"]
        phase_lbl.config(text=phase, fg=theme.FG2 if phase not in {"done", "failed"} else theme.GREEN)
        pb.config(mode="determinate", value=fraction * 100)
        self._current_phase = phase
        if fraction > 0 and status_lbl.cget("text") == "Queued":
            status_lbl.config(text="RUNNING", fg=theme.ACCENT)
            row["state"] = "running"
            self._sync_skip_button_state(row)
            self._refresh_queue_filter_counts()

    def _callbacks(self) -> JobCallbacks:
        return JobCallbacks(
            log_cb=lambda message, colour=None: self._ui_queue.put(("log", (f"[{self._time()}] {message}", colour or theme.FG))),
            progress_overall_cb=lambda fraction: self._ui_queue.put(("overall", fraction)),
            progress_case_cb=lambda _fraction: None,
            status_cb=lambda text: self._ui_queue.put(("status", text)),
            queue_cb=lambda items: self._ui_queue.put(("queue", items)),
            item_status_cb=lambda idx, state: self._ui_queue.put(("item_status", (idx, state))),
            item_progress_cb=lambda idx, fraction, phase: self._ui_queue.put(("item_progress", (idx, fraction, phase))),
            item_failure_cb=lambda idx, reason: self._ui_queue.put(("item_failure", (idx, reason))),
            verbose_cb=lambda text: self._ui_queue.put(("verbose", text)),
        )

    def _time(self):
        import datetime as dt

        return dt.datetime.now().strftime("%H:%M:%S")

    def _build_config(
        self,
        algorithms: list[str],
        case_metadata: dict[str, str] | None,
        selected_item_names: list[str] | None,
    ) -> JobConfig:
        return JobConfig(
            source_dir=Path(self._src_var.get().strip()),
            output_dir=Path(self._dst_var.get().strip()),
            archive_fmt=self._fmt_var.get(),
            compress_level_label=self._level_var.get(),
            split_enabled=self._split_enabled.get(),
            split_size_str=self._split_size_var.get(),
            hash_algorithms=algorithms,
            password=self._pw_var.get().strip() or None,
            delete_source=self._delete_src_var.get(),
            skip_existing=self._skip_existing_var.get(),
            case_metadata=case_metadata,
            threads=self._threads_var.get(),
            scan_mode="fast" if self._fast_scan_var.get() else "deterministic",
            archive_hash_mode="skip" if (not algorithms or self._skip_archive_hash_var.get()) else "always",
            thread_strategy="auto" if self._auto_threads_var.get() else "fixed",
            progress_interval_ms=max(0, int(self._progress_interval_var.get())),
            resume_enabled=self._resume_var.get(),
            dry_run=self._dry_run_var.get(),
            state_db_path=Path(self._state_db_var.get().strip()) if self._state_db_var.get().strip() else None,
            report_json=self._report_json_var.get(),
            embed_manifest_in_archive=self._embed_manifest_in_archive_var.get(),
            selected_item_names=selected_item_names,
            hash_threads=max(1, int(self._hash_threads_var.get())),
        )

    def _confirm_session_summary(self, config: JobConfig) -> bool:
        summary = build_run_summary(config)
        return messagebox.askokcancel("Session Summary", summary)

    def _confirm_destructive_action(self, config: JobConfig) -> bool:
        if not requires_destructive_confirmation(config):
            return True
        value = simpledialog.askstring(
            "Confirm Destructive Action",
            "Delete source is enabled.\nType DELETE to continue.",
            parent=self,
        )
        if not validate_destructive_confirmation(value):
            messagebox.showwarning("Confirmation Required", "Run cancelled. DELETE confirmation did not match.")
            return False
        return True

    def _collect_settings_payload(self) -> dict[str, object]:
        hashes = self._selected_hash_algorithms()
        return {
            "source_dir": self._src_var.get().strip(),
            "output_dir": self._dst_var.get().strip(),
            "archive_fmt": self._fmt_var.get(),
            "compress_level_label": self._level_var.get(),
            "hash_algorithms": hashes,
            "threads": int(self._threads_var.get()),
            "auto_threads": bool(self._auto_threads_var.get()),
            "split_enabled": bool(self._split_enabled.get()),
            "split_size_str": self._split_size_var.get().strip(),
            "delete_source": bool(self._delete_src_var.get()),
            "skip_existing": bool(self._skip_existing_var.get()),
            "resume_enabled": bool(self._resume_var.get()),
            "dry_run": bool(self._dry_run_var.get()),
            "scan_mode": "fast" if self._fast_scan_var.get() else "deterministic",
            "archive_hash_mode": "skip" if (hashes and self._skip_archive_hash_var.get()) else "always",
            "thread_strategy": "auto" if self._auto_threads_var.get() else "fixed",
            "progress_interval_ms": int(self._progress_interval_var.get()),
            "state_db_path": self._state_db_var.get().strip(),
            "report_json": bool(self._report_json_var.get()),
            "embed_manifest_in_archive": bool(self._embed_manifest_in_archive_var.get()),
            "hash_threads": max(1, int(self._hash_threads_var.get())),
            "use_metadata": bool(self._use_metadata_var.get()),
            "metadata_examiner": self._meta_vars["Examiner"].get(),
            "metadata_case_id": self._meta_vars["Case ID"].get(),
            "metadata_evidence_id": self._meta_vars["Evidence ID"].get(),
            "metadata_notes": self._meta_vars["Notes"].get(),
        }

    def _save_current_settings(self):
        save_gui_settings(self._collect_settings_payload())

    def _update_last_report_path(self):
        output_text = self._dst_var.get().strip()
        if not output_text:
            return
        output_path = Path(output_text)
        if not output_path.exists():
            return
        candidates = sorted(output_path.glob("ForensicPack_Report_*.txt"), key=lambda item: item.stat().st_mtime)
        if candidates:
            self._last_report_path = str(candidates[-1])
            self._last_output_dir = str(output_path)

    def _start(self):
        if not self._src_var.get().strip() or not self._dst_var.get().strip():
            messagebox.showerror("Missing Paths", "Please select both Source and Output folders.")
            return
        source_dir = Path(self._src_var.get().strip())
        if not source_dir.is_dir():
            messagebox.showerror("Invalid Source", f"Source folder not found:\n{source_dir}")
            return
        algorithms = self._selected_hash_algorithms()

        case_metadata = None
        if self._use_metadata_var.get():
            case_metadata = {key: value.get() for key, value in self._meta_vars.items()}

        selected_item_names = self._resolved_selected_items(source_dir)
        if selected_item_names is not None and not selected_item_names:
            messagebox.showerror("No Items Selected", "No selected items are currently available in source.")
            return
        config = self._build_config(algorithms, case_metadata, selected_item_names)
        if not self._confirm_session_summary(config):
            return
        if not self._confirm_destructive_action(config):
            return
        self._save_current_settings()

        self._token = CancellationToken()
        self._running_jobs.clear()
        self._run_started_at = time.monotonic()
        self._current_phase = "startup"
        self._set_mutable_controls_enabled(False)
        self._start_btn.config(state="disabled")
        self._cancel_btn.config(state="normal", bg=theme.RED, fg=theme.WHITE)
        self._prog_overall.config(value=0)
        self._last_elapsed_seconds = 0.0
        self._status_var.set("Running")

        def _worker():
            try:
                run_session(config, self._callbacks(), self._token)
            except Exception as exc:
                if is_permission_error(exc):
                    self._ui_queue.put(("permission_error", str(exc)))
                    self._ui_queue.put(("log", (f"[{self._time()}] [PERMISSION] {exc}", theme.YELLOW)))
                else:
                    self._ui_queue.put(("log", (f"[{self._time()}] [FATAL] {exc}", theme.RED)))
            finally:
                self._ui_queue.put(("done", None))

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def _done(self):
        self._set_mutable_controls_enabled(True)
        self._start_btn.config(state="normal")
        self._cancel_btn.config(state="disabled", bg=theme.BG3, fg=theme.RED)
        self._disable_all_skip_buttons()
        self._current_phase = "complete"
        self._run_started_at = None
        self._update_last_report_path()
        self._on_format_changed()
        self._refresh_archive_hash_option_state()
        self._on_metadata_toggle()
        self._status_var.set("Ready")
        self._notify_completion()

    def _notify_completion(self):
        """Sends a Windows toast notification on completion."""
        try:
            total = len(self._queue_rows)
            failed = sum(1 for r in self._queue_rows if r["state"] == "error")
            warnings = sum(1 for r in self._queue_rows if r["state"] == "warning")
            msg = f"Processed {total} items. ({failed} failures, {warnings} warnings)"
            # Use powershell for 0-dependency toast notification
            ps_cmd = (
                f'[void][reflection.assembly]::LoadWithPartialName("System.Windows.Forms");'
                f'$t = New-Object System.Windows.Forms.NotifyIcon;'
                f'$t.Icon = [System.Drawing.SystemIcons]::Information;'
                f'$t.Visible = $true;'
                f'$t.ShowBalloonTip(5000, "ForensicPack Complete", "{msg}", [System.Windows.Forms.ToolTipIcon]::Info);'
                f'Sleep 5;$t.Dispose()'
            )
            subprocess.Popen(["powershell", "-Command", ps_cmd], creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass

    def _toggle_theme(self):
        theme.toggle_theme()
        self._theme_btn.config(text="\u263c" if theme.current_theme_name == "dark" else "\u263e")
        self._refresh_ui_colors()
        self._save_current_settings()

    def _refresh_ui_colors(self):
        self.configure(bg=theme.BG)
        self._apply_style()

        def _recursive_refresh(widget):
            try:
                # Update standard tk widgets that don't follow ttk styles
                w_type = widget.winfo_class()
                if w_type == "Frame":
                    # Check if it's a specific container or card
                    bg_val = widget.cget("bg")
                    if bg_val in {theme._themes["dark"]["BG"], theme._themes["light"]["BG"]}:
                        widget.config(bg=theme.BG)
                    elif bg_val in {theme._themes["dark"]["BG2"], theme._themes["light"]["BG2"]}:
                        widget.config(bg=theme.BG2)
                    elif bg_val in {theme._themes["dark"]["BG3"], theme._themes["light"]["BG3"]}:
                        widget.config(bg=theme.BG3)
                elif w_type == "Label":
                    if widget.cget("bg") in {theme._themes["dark"]["BG2"], theme._themes["light"]["BG2"]}:
                        widget.config(bg=theme.BG2, fg=theme.FG if "title" not in str(widget).lower() else theme.WHITE)
                    else:
                        widget.config(bg=theme.BG, fg=theme.FG)
                elif w_type == "Button":
                    # Theme button/controls
                    if widget == self._theme_btn:
                        widget.config(bg=theme.BG2, fg=theme.FG2, activebackground=theme.BG3)
                    elif widget.cget("bg") in {theme._themes["dark"]["BG3"], theme._themes["light"]["BG3"]}:
                        widget.config(bg=theme.BG3, fg=theme.ACCENT if "copy" in str(widget).lower() or "open" in str(widget).lower() else theme.FG2)
                elif w_type == "Entry":
                    widget.config(bg=theme.BG3, fg=theme.WHITE, insertbackground=theme.WHITE, highlightbackground=theme.BORDER)
                elif w_type == "Text":
                    widget.config(bg=theme.BG, fg=theme.FG, highlightbackground=theme.BORDER)
                elif "Progressbar" in w_type:
                    # Force style re-application for ttk widgets
                    current_style = widget.cget("style")
                    if not current_style:
                        widget.config(style="Horizontal.TProgressbar")
                    else:
                        widget.config(style=current_style)
            except Exception:
                pass

            for child in widget.winfo_children():
                _recursive_refresh(child)

        _recursive_refresh(self)
        # Special case for queue rows which are rebuilt on filter, but we want immediate update
        self._apply_queue_filter()

    def _cancel(self):
        if self._token:
            self._token.request_cancel()
        self._cancel_btn.config(state="disabled")
        self._disable_all_skip_buttons()
        removed = self._cleanup_temp_outputs()
        if removed:
            self._ui_queue.put(("log", (f"[{self._time()}] [CANCEL] Removed {removed} temporary file(s).", theme.YELLOW)))
        self._status_var.set("Cancelling")

    def _cleanup_temp_outputs(self) -> int:
        output_text = self._dst_var.get().strip()
        if not output_text:
            return 0
        output_dir = Path(output_text)
        if not output_dir.exists() or not output_dir.is_dir():
            return 0
        removed = 0
        for pattern in ("*.partial", "*.partial.*", "tmp_*_manifest.txt"):
            for path in output_dir.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def on_close(self):
        if self._thread and self._thread.is_alive():
            if messagebox.askyesno("Exit", "Processing is running. Force quit?"):
                if self._token:
                    self._token.request_cancel()
                self._cleanup_temp_outputs()
                self.update_idletasks()
                self.destroy()
        else:
            self.destroy()



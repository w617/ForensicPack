import os
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui_components.common import FONT_HEAD, FONT_MAIN, FONT_TITLE, ScrollableFrame
from gui_components.themes import theme
from utils import application_data_dir, metadata_output_dir


def _open_in_shell(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def apply_ui_refresh(app_class) -> None:
    """Apply the v2.2 examiner-focused layout without changing the engine API."""
    if getattr(app_class, "_ui_refresh_applied", False):
        return
    app_class._ui_refresh_applied = True

    original_card = app_class._card
    original_advanced = app_class._build_advanced_options_panel
    original_update_last_report = app_class._update_last_report_path

    def card(self, parent, title):
        title_map = {
            "Source & Destination": "1. Evidence & Destination",
            "Basic Archive Setup": "2. Package Settings",
            "Hash Algorithms": "3. Integrity",
            "Case Metadata (Optional)": "Case Details (Optional)",
            "Advanced Runtime Policies": "Advanced Settings",
        }
        return original_card(self, parent, title_map.get(title, title))

    def open_application_data(self):
        try:
            _open_in_shell(application_data_dir())
        except Exception as exc:
            messagebox.showerror("Open Failed", f"Could not open the ForensicPack data folder:\n{exc}")

    def current_metadata_dir(self) -> Path:
        output_text = self._dst_var.get().strip() if hasattr(self, "_dst_var") else ""
        if not output_text:
            return application_data_dir()
        return metadata_output_dir(Path(output_text))

    def open_metadata_folder(self):
        try:
            _open_in_shell(current_metadata_dir(self))
        except Exception as exc:
            messagebox.showerror("Open Failed", f"Could not open the metadata folder:\n{exc}")

    def toggle_advanced(self):
        visible = bool(getattr(self, "_advanced_visible", False))
        if visible:
            self._advanced_host.pack_forget()
            self._advanced_visible = False
            self._advanced_toggle_btn.config(text="Show Advanced Settings  ▾")
        else:
            self._advanced_host.pack(fill="x", after=self._advanced_toggle_row)
            self._advanced_visible = True
            self._advanced_toggle_btn.config(text="Hide Advanced Settings  ▴")

    def build_advanced(self, parent):
        original_advanced(self, parent)
        if hasattr(self, "_state_db_entry"):
            row = self._state_db_entry.master
            for child in row.winfo_children():
                if isinstance(child, tk.Label) and str(child.cget("text")).startswith("State DB"):
                    child.config(text="Resume DB override:")
                elif isinstance(child, tk.Button) and child.cget("text") == "Default":
                    child.config(text="Use App Default")
            tk.Label(
                parent,
                text=f"Default resume database: {application_data_dir() / 'forensicpack_state.db'}",
                fg=theme.FG2,
                bg=theme.BG,
                font=("Segoe UI", 9),
                wraplength=430,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 8))

        # Move the destructive option out of the normal intake card.
        if hasattr(self, "_delete_cb"):
            try:
                self._delete_cb.master.pack_forget()
            except tk.TclError:
                pass
        danger = self._card(parent, "Danger Zone")
        delete_cb = tk.Checkbutton(
            danger,
            text="Delete source item only after successful verification",
            variable=self._delete_src_var,
            bg=theme.BG2,
            fg=theme.RED,
            activebackground=theme.BG2,
            activeforeground=theme.WHITE,
            selectcolor=theme.BG3,
            font=FONT_MAIN,
            cursor="hand2",
        )
        delete_cb.pack(anchor="w")
        self._delete_cb = delete_cb
        self._register_mutable(delete_cb)
        self._verify_disabled_widgets.append((delete_cb, "normal"))
        tk.Label(
            danger,
            text="Leave this off for normal evidence packaging. Enabling it requires a DELETE confirmation before processing.",
            fg=theme.FG2,
            bg=theme.BG2,
            font=("Segoe UI", 9),
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def build_controls(self, parent):
        ctrl = tk.Frame(parent, bg=parent.cget("bg"))
        ctrl.pack(fill="x", pady=6)
        actions = tk.Frame(ctrl, bg=parent.cget("bg"))
        actions.pack(side="left")

        def action_button(text, command, accent=False):
            button = tk.Button(
                actions,
                text=text,
                command=command,
                bg=theme.ACCENT if accent else theme.BG3,
                fg=theme.BG if accent else theme.ACCENT,
                font=("Segoe UI", 9, "bold" if accent else "normal"),
                relief="flat",
                activebackground=theme.BORDER,
                activeforeground=theme.WHITE,
                cursor="hand2",
                padx=12,
                pady=8,
            )
            button.pack(side="left", padx=(0, 8))
            return button

        self._open_output_btn = action_button("Open Destination", self._open_output_folder, accent=True)
        self._open_metadata_btn = action_button("Open Metadata", self._open_metadata_folder)
        self._open_report_btn = action_button("Open Last Report", self._open_last_report)

        more_btn = tk.Menubutton(
            actions,
            text="More Actions  ▾",
            bg=theme.BG3,
            fg=theme.FG,
            font=("Segoe UI", 9),
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=8,
        )
        more_menu = tk.Menu(more_btn, tearoff=False)
        more_menu.add_command(label="Copy Last Report Path", command=self._copy_last_report_path)
        more_menu.add_command(label="Show Last Summary", command=self._show_last_completion_summary)
        more_menu.add_separator()
        more_menu.add_command(label="Verbose Console", command=self._open_verbose)
        more_menu.add_command(label="Copy Diagnostic Snapshot", command=self._copy_diagnostic_snapshot)
        more_menu.add_command(label="Run Diagnostics", command=self._run_diagnostics)
        more_menu.add_command(label="Relaunch as Administrator", command=self._manual_uac_relaunch)
        more_menu.add_separator()
        more_menu.add_command(label="Clear Live Log", command=self._clear_log)
        more_menu.add_command(label="Save Live Log", command=self._save_log)
        more_menu.add_command(label="Reset Saved Settings", command=self._reset_saved_settings)
        more_btn.config(menu=more_menu)
        more_btn.pack(side="left")
        self._more_actions_btn = more_btn

        # Preserve legacy widget attributes used by existing code/tests.
        self._copy_report_btn = more_btn
        self._summary_btn = more_btn
        self._diag_snapshot_btn = more_btn
        self._verbose_btn = more_btn
        self._diag_btn = more_btn
        self._uac_btn = more_btn
        self._clear_btn = more_btn
        self._save_log_btn = more_btn

        stats = tk.Frame(ctrl, bg=parent.cget("bg"))
        stats.pack(side="right")
        self._status_var = tk.StringVar(value="Ready")
        self._stats_var = tk.StringVar(value="Processed 0/0 | Phase: idle | Elapsed: 00:00 | ETA: --:--")
        tk.Label(stats, textvariable=self._stats_var, fg=theme.FG2, bg=theme.BG, font=("Segoe UI", 9)).pack(side="right")
        tk.Label(stats, textvariable=self._status_var, fg=theme.FG2, bg=theme.BG, font=("Segoe UI", 9, "bold")).pack(side="right", padx=(0, 12))

    def build_ui(self):
        self.geometry("1180x720")
        self.minsize(1040, 640)

        title_frame = tk.Frame(self, bg=theme.BG2, height=64)
        title_frame.pack(fill="x", side="top")
        title_frame.pack_propagate(False)
        has_banner = self._build_title_brand(title_frame)
        if not has_banner:
            tk.Label(title_frame, text=self.title().split(" v")[0], font=FONT_TITLE, fg=theme.WHITE, bg=theme.BG2).pack(side="left", pady=10, padx=(16, 2))
        tk.Label(title_frame, text=f"v{self.title().split(' v')[-1] if ' v' in self.title() else ''}", font=FONT_MAIN, fg=theme.FG2, bg=theme.BG2).pack(side="left", padx=8, pady=10)
        self._theme_btn = tk.Button(
            title_frame,
            text="☾",
            font=("Segoe UI", 14),
            fg=theme.FG2,
            bg=theme.BG2,
            relief="flat",
            bd=0,
            activebackground=theme.BG3,
            activeforeground=theme.WHITE,
            cursor="hand2",
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=12, pady=10)
        tk.Label(
            title_frame,
            text="Evidence Packaging & Verification",
            font=("Segoe UI", 10, "bold"),
            fg=theme.PURPLE,
            bg=theme.BG2,
        ).pack(side="right", padx=8, pady=10)

        storage_bar = tk.Frame(self, bg=theme.BG3, highlightthickness=1, highlightbackground=theme.BORDER)
        storage_bar.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(
            storage_bar,
            text="Clean destination mode",
            font=("Segoe UI", 9, "bold"),
            fg=theme.GREEN,
            bg=theme.BG3,
        ).pack(side="left", padx=(12, 6), pady=7)
        tk.Label(
            storage_bar,
            text="Only archives are written to the selected destination. Reports, manifests, audit logs, checksums, and resume data stay in ForensicPack application data.",
            font=("Segoe UI", 9),
            fg=theme.FG2,
            bg=theme.BG3,
        ).pack(side="left", fill="x", expand=True, pady=7)
        tk.Button(
            storage_bar,
            text="Open Data Folder",
            command=self._open_application_data,
            bg=theme.BG3,
            fg=theme.ACCENT,
            relief="flat",
            cursor="hand2",
            padx=10,
        ).pack(side="right", padx=8, pady=4)

        footer = tk.Frame(self, bg=theme.BG, bd=0, highlightthickness=1, highlightbackground=theme.BORDER)
        footer.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        self._build_controls(footer)

        self._paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        self._paned.pack(fill="both", expand=True, padx=16, pady=(10, 4))
        left_container = ttk.Frame(self._paned)
        self._paned.add(left_container, weight=1)
        scroll_frame = ScrollableFrame(left_container, bg=theme.BG)
        scroll_frame.pack(fill="both", expand=True)
        left = scroll_frame.scroll_window
        right = ttk.Frame(self._paned)
        self._paned.add(right, weight=2)
        self._left_root = left

        self._build_paths_panel(left)
        self._build_archive_panel(left)
        self._build_hash_panel(left)
        self._build_case_panel(left)

        self._advanced_toggle_row = tk.Frame(left, bg=theme.BG)
        self._advanced_toggle_row.pack(fill="x", pady=(0, 10))
        self._advanced_toggle_btn = tk.Button(
            self._advanced_toggle_row,
            text="Show Advanced Settings  ▾",
            command=self._toggle_advanced_settings,
            bg=theme.BG3,
            fg=theme.ACCENT,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            activebackground=theme.BORDER,
            activeforeground=theme.WHITE,
            cursor="hand2",
            padx=12,
            pady=7,
        )
        self._advanced_toggle_btn.pack(fill="x")
        self._advanced_host = tk.Frame(left, bg=theme.BG)
        self._advanced_host.pack(fill="x")
        self._build_advanced_options_panel(self._advanced_host)
        self._advanced_visible = False
        self._advanced_host.pack_forget()

        self._build_queue_panel(right)
        self._build_log_panel(right)
        self._apply_mode_state()

    def update_last_report_path(self):
        output_text = self._dst_var.get().strip()
        if not output_text:
            return
        output_path = Path(output_text)
        metadata_path = metadata_output_dir(output_path)
        if not metadata_path.exists():
            return
        candidates = sorted(metadata_path.glob("ForensicPack_Report_*.txt"), key=lambda item: item.stat().st_mtime)
        if candidates:
            self._last_report_path = str(candidates[-1])
            self._last_output_dir = str(output_path)
        else:
            original_update_last_report(self)

    def browse_state_db_btn(self, parent):
        def pick():
            default_path = application_data_dir() / "forensicpack_state.db"
            current = self._state_db_var.get().strip() if hasattr(self, "_state_db_var") else ""
            current_path = Path(current) if current else default_path
            selected = filedialog.asksaveasfilename(
                title="Select Resume Database Path",
                initialdir=str(current_path.parent),
                initialfile=current_path.name,
                defaultextension=".db",
                filetypes=[("SQLite DB", "*.db *.sqlite *.sqlite3"), ("All Files", "*.*")],
            )
            if selected:
                self._state_db_var.set(selected)

        button = tk.Button(
            parent,
            text="Browse ...",
            command=pick,
            bg=theme.BG3,
            fg=theme.ACCENT,
            font=FONT_MAIN,
            relief="flat",
            activebackground=theme.ACCENT,
            activeforeground=theme.BG,
            cursor="hand2",
            padx=10,
        )
        button.pack(side="right", padx=(6, 0))
        self._register_mutable(button)
        if hasattr(self, "_verify_disabled_widgets"):
            self._verify_disabled_widgets.append((button, "normal"))
        return button

    app_class._card = card
    app_class._build_ui = build_ui
    app_class._build_controls = build_controls
    app_class._build_advanced_options_panel = build_advanced
    app_class._toggle_advanced_settings = toggle_advanced
    app_class._open_application_data = open_application_data
    app_class._current_metadata_dir = current_metadata_dir
    app_class._open_metadata_folder = open_metadata_folder
    app_class._update_last_report_path = update_last_report_path
    app_class._browse_state_db_btn = browse_state_db_btn

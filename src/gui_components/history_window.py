"""Search and act on retained runs without blocking the Tk event loop."""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from history import HistoryStore
from models import CancellationToken
from verification_center import export_records, reverify_run


def open_file(path: str):
    if not path or not Path(path).is_file():
        raise ValueError("The selected record is missing or was not generated.")
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


class HistoryWindow(tk.Toplevel):
    def __init__(self, parent, verify_temp_dir=None):
        super().__init__(parent)
        self.title("ForensicPack — Job History & Verification")
        self.geometry("1140x670")
        self.minsize(820, 480)
        self.store = HistoryStore()
        self.verify_temp_dir = verify_temp_dir
        self.offset = 0
        self.rows = {}
        self.messages = queue.Queue()
        self.worker = None
        self.token = None
        self.closing = False
        self.query = tk.StringVar()
        self.filter = tk.StringVar(value="All")
        self.status = tk.StringVar(value="Ready")
        self._search_timer = None
        search = ttk.Frame(self, padding=10)
        search.pack(fill="x")
        ttk.Label(search, text="Search case, evidence ID, item, archive, or run:").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        choices = ["All", "success", "warning", "failed", "cancelled", "skipped", "running/incomplete"]
        combo = ttk.Combobox(search, textvariable=self.filter, values=choices, state="readonly", width=20)
        combo.pack(side="right")
        self.query.trace_add("write", self._search_changed)
        combo.bind("<<ComboboxSelected>>", self._search_changed)
        columns = ("started", "case", "evidence", "item", "status", "verify", "last")
        frame = ttk.Frame(self, padding=(10, 0))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for key, label, width in zip(columns, ["Started (UTC)", "Case ID", "Evidence ID", "Item", "Original status", "Content verify", "Latest check"],
                                     [175, 100, 100, 190, 115, 115, 100]):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=70)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_details)
        self.details = tk.Text(self, height=7, wrap="word", state="disabled")
        self.details.pack(fill="x", padx=10, pady=8)
        actions = ttk.Frame(self, padding=(10, 0))
        actions.pack(fill="x")
        self.buttons = []
        for label, action in [("Open Log", self.open_log), ("Open Report", self.open_report),
                              ("Reverify", self.reverify), ("Export Records", self.export),
                              ("Import Prior Runs", self.import_previous), ("Refresh", self.refresh)]:
            button = ttk.Button(actions, text=label, command=action)
            button.pack(side="left", padx=(0, 6))
            self.buttons.append(button)
        self.cancel_button = ttk.Button(actions, text="Cancel Task", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="right")
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Button(footer, text="Next", command=lambda: self.page(200)).pack(side="right")
        ttk.Button(footer, text="Previous", command=lambda: self.page(-200)).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self._poll_id = self.after(100, self.poll)

    def _search_changed(self, *_):
        if self._search_timer:
            self.after_cancel(self._search_timer)
        self.offset = 0
        self._search_timer = self.after(250, self.refresh)

    def page(self, change):
        self.offset = max(0, self.offset + change)
        self.refresh()

    def refresh(self):
        self._search_timer = None
        try:
            rows, total = self.store.search(self.query.get(), self.filter.get(), offset=self.offset)
            if not rows and self.offset:
                self.offset = max(0, self.offset - 200)
                rows, total = self.store.search(self.query.get(), self.filter.get(), offset=self.offset)
            self.rows = {r["run_id"]: r for r in rows}
            self.tree.delete(*self.tree.get_children())
            for row in rows:
                self.tree.insert("", "end", iid=row["run_id"], values=(row["started_utc"], row["case_id"],
                    row["evidence_id"], row["case_name"], row["status"], row["content_verification"], row["last_check"] or "Not checked"))
            self.status.set(f"Showing {len(rows)} of {total} runs. Import Prior Runs indexes retained v2.2.2 records.")
        except Exception as exc:
            messagebox.showerror("History unavailable", str(exc), parent=self)

    def selected(self):
        selected = self.tree.selection()
        if not selected:
            raise ValueError("Select a run first.")
        return self.store.get(selected[0])

    def show_details(self, *_):
        try:
            row = self.selected()
            warnings = "\n".join(json.loads(row["warnings_json"]))
            text = (f"Run: {row['run_id']}\nArchive: {row['archive_path']}\nSource: {row['source_path']}\n"
                    f"Original verification: {row['verification']}\nRecords: {row['run_dir']}\n{warnings}")
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", text)
            self.details.configure(state="disabled")
        except ValueError:
            pass

    def open_log(self):
        try:
            open_file(self.selected()["audit_path"])
        except Exception as exc:
            messagebox.showerror("Open Log", str(exc), parent=self)

    def open_report(self):
        try:
            row = self.selected()
            events = self.store.events(row["run_id"])
            reports = json.loads(row["reports_json"])
            # Prefer latest reverification report; otherwise the original TXT report.
            path = events[-1]["report_path"] if events else next((p for p in reports if p.endswith(".txt")), "")
            open_file(path)
        except Exception as exc:
            messagebox.showerror("Open Report", str(exc), parent=self)

    def start_task(self, description, action):
        if self.worker and self.worker.is_alive():
            return
        self.token = CancellationToken()
        for button in self.buttons:
            button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.status.set(description)
        token = self.token

        def work():
            try:
                self.messages.put((True, action(token)))
            except Exception as exc:
                self.messages.put((False, str(exc) or type(exc).__name__))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def reverify(self):
        try:
            row = self.selected()
        except ValueError as exc:
            messagebox.showinfo("Reverify", str(exc), parent=self)
            return
        folder = filedialog.askdirectory(parent=self, title="Archive folder (choose its new location if moved)",
                                          initialdir=str(Path(row["archive_path"]).parent), mustexist=True)
        if not folder:
            return
        password = None
        if row["archive_format"] == "7z":
            password = simpledialog.askstring("Archive password", "Password, or leave blank for an unencrypted archive:",
                                             show="*", parent=self)
            if password is None:
                return
        self.start_task("Reverifying archive and retained records…", lambda token: reverify_run(
            row["run_id"], archive_dir=Path(folder), verify_temp_dir=self.verify_temp_dir,
            password=password or None, token=token, store=self.store))

    def export(self):
        ids = list(self.tree.selection())
        if not ids:
            messagebox.showinfo("Export Records", "Select one or more runs first.", parent=self)
            return
        destination = filedialog.asksaveasfilename(parent=self, title="Export verification records (no evidence archives)",
            defaultextension=".zip", initialfile="ForensicPack_Verification_Records.zip", filetypes=[("ZIP", "*.zip")])
        if destination:
            self.start_task("Exporting retained records and session reports…", lambda token: str(export_records(
                ids, Path(destination), store=self.store, token=token)))

    def import_previous(self):
        self.start_task("Indexing prior retained runs…", lambda token: f"Imported {self.store.import_previous_runs(token)} runs.")

    def cancel(self):
        if self.token:
            self.token.request_cancel()
            self.status.set("Cancelling; waiting for temporary-file cleanup…")

    def poll(self):
        try:
            ok, result = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            for button in self.buttons:
                button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.refresh()
            if not self.closing:
                if ok:
                    detail = json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
                    messagebox.showinfo("Verification Center", detail, parent=self)
                else:
                    messagebox.showerror("Task did not complete", result, parent=self)
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self.destroy()
            return
        self._poll_id = self.after(100, self.poll)

    def close(self):
        if self._search_timer:
            self.after_cancel(self._search_timer)
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.cancel()
        else:
            self.after_cancel(self._poll_id)
            self.destroy()

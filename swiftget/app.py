"""SwiftGet Download Manager - Tkinter GUI."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import bridge
import engine
from engine import (ACTIVE_STATES, COMPLETE, ERROR, PAUSED, QUEUED,
                    human_size, human_speed, human_time)

APP_NAME = "SwiftGet Download Manager"
VERSION = "1.0.0"

# palette
ACCENT = "#1f6feb"
ACCENT_DARK = "#174ea6"
BG = "#f4f6fa"
PANEL = "#ffffff"
SEG_COLORS = ["#1f6feb", "#2da44e", "#bf8700", "#cf222e", "#8250df",
              "#0598bc", "#d1242f", "#57606a"]


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def data_dir():
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        root = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(root, "SwiftGet")


def open_path(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as e:
        messagebox.showerror(APP_NAME, f"Cannot open:\n{path}\n\n{e}")


def open_folder(path):
    if sys.platform == "win32" and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


def set_icon(win):
    try:
        if sys.platform == "win32":
            win.iconbitmap(resource("icon.ico"))
        else:
            win.iconphoto(True, tk.PhotoImage(file=resource("icon.png")))
    except tk.TclError:
        pass


def make_modal(win, tries=60):
    """grab_set() fails with "window not viewable" if the dialog is not on
    screen yet (e.g. the main window was minimized). Retry until it is
    visible instead of crashing and leaving a half-built dialog behind."""
    try:
        if not win.winfo_exists():
            return
        win.grab_set()
        win.focus_force()
    except tk.TclError:
        if tries > 0:
            win.after(150, lambda: make_modal(win, tries - 1))


def center(win, parent=None):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if parent is not None:
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    else:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


# ===================================================================
class AddUrlDialog(tk.Toplevel):
    """Step 1: enter the address."""

    def __init__(self, app, url=""):
        super().__init__(app.root)
        self.app = app
        self.title("Enter new address to download")
        self.resizable(False, False)
        self.transient(app.root)
        set_icon(self)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Address:").grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=url or self._clipboard_url())
        e = ttk.Entry(f, textvariable=self.var, width=70)
        e.grid(row=1, column=0, columnspan=3, sticky="we", pady=(4, 10))
        e.focus_set()
        e.select_range(0, "end")
        ttk.Label(f, text="Description (optional):").grid(row=2, column=0, sticky="w")
        self.desc = tk.StringVar()
        ttk.Entry(f, textvariable=self.desc, width=70).grid(
            row=3, column=0, columnspan=3, sticky="we", pady=(4, 12))
        ttk.Button(f, text="OK", style="Accent.TButton", command=self.ok).grid(
            row=4, column=1, sticky="e", padx=4)
        ttk.Button(f, text="Cancel", command=self.destroy).grid(row=4, column=2, sticky="e")
        f.columnconfigure(0, weight=1)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        center(self, app.root)
        make_modal(self)

    def _clipboard_url(self):
        try:
            t = self.app.root.clipboard_get().strip()
            if t.lower().startswith(("http://", "https://", "ftp://")) and " " not in t:
                return t
        except tk.TclError:
            pass
        return ""

    def ok(self):
        url = self.var.get().strip()
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showwarning(APP_NAME, "Please enter a valid http:// or https:// address.",
                                   parent=self)
            return
        desc = self.desc.get().strip()
        self.destroy()
        FileInfoDialog(self.app, url, desc)


# ===================================================================
class FileInfoDialog(tk.Toplevel):
    """Step 2: show file info, choose where to save, start or queue."""

    def __init__(self, app, url, description="", headers=None, filename="", size=-1):
        super().__init__(app.root)
        self.app = app
        self.url = url
        self.description = description
        self.headers = headers or {}
        self.suggested = engine.sanitize_filename(filename) if filename else ""
        self.info = None
        self.title("Download File Info")
        self.resizable(False, False)
        self.transient(app.root)
        set_icon(self)
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="URL:").grid(row=0, column=0, sticky="w")
        ttk.Label(f, text=url if len(url) < 80 else url[:77] + "…",
                  foreground="#555").grid(row=0, column=1, columnspan=3, sticky="w")

        ttk.Label(f, text="Category:").grid(row=1, column=0, sticky="w", pady=6)
        self.cat = tk.StringVar(value="General")
        cats = ["General"] + list(engine.CATEGORIES)
        cb = ttk.Combobox(f, textvariable=self.cat, values=cats, state="readonly", width=18)
        cb.grid(row=1, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self._category_changed())

        ttk.Label(f, text="Save As:").grid(row=2, column=0, sticky="w")
        self.path = tk.StringVar(value=os.path.join(app.mgr.settings["save_dir"], "…"))
        ttk.Entry(f, textvariable=self.path, width=62).grid(row=2, column=1, columnspan=2,
                                                            sticky="we")
        ttk.Button(f, text="…", width=3, command=self.browse).grid(row=2, column=3, padx=(4, 0))

        self.size_lbl = ttk.Label(f, text="Size: requesting file information…")
        self.size_lbl.grid(row=3, column=1, columnspan=3, sticky="w", pady=(8, 2))
        self.res_lbl = ttk.Label(f, text="", foreground="#555")
        self.res_lbl.grid(row=4, column=1, columnspan=3, sticky="w")

        self.remember = tk.BooleanVar(value=True)
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=4, sticky="e", pady=(14, 0))
        self.start_btn = ttk.Button(bf, text="Start Download", style="Accent.TButton",
                                    command=lambda: self.go(True))
        self.start_btn.pack(side="left", padx=4)
        ttk.Button(bf, text="Download Later", command=lambda: self.go(False)).pack(
            side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        f.columnconfigure(1, weight=1)
        self.bind("<Escape>", lambda e: self.destroy())
        center(self, app.root)
        make_modal(self)

        self._filename = self.suggested or os.path.basename(url.split("?")[0]) or "download"
        self._set_name(self._filename)
        if size and size > 0:
            self.size_lbl.config(text=f"Size: {human_size(size)}")
        if self.headers:
            self.res_lbl.config(text="Captured from browser - checking resume capability…")
        threading.Thread(target=self._probe, daemon=True).start()

    def _set_name(self, name):
        self._filename = engine.sanitize_filename(name)
        self.cat.set(engine.category_for(self._filename))
        self.path.set(os.path.join(self.app.mgr.dir_for(self._filename), self._filename))

    def _category_changed(self):
        name = os.path.basename(self.path.get())
        base = self.app.mgr.settings["save_dir"]
        sub = self.cat.get()
        d = os.path.join(base, sub) if self.app.mgr.settings.get("sort_by_category") else base
        self.path.set(os.path.join(d, name))

    def _probe(self):
        try:
            info = engine.probe(self.url, headers=self.headers)
        except Exception as e:  # noqa: BLE001
            info = {"error": engine._err_text(e)}
        try:
            self.after(0, self._probed, info)
        except (tk.TclError, RuntimeError):
            pass

    def _probed(self, info):
        if not self.winfo_exists():
            return
        if "error" in info:
            self.size_lbl.config(text=f"Could not get file info: {info['error']}",
                                 foreground="#cf222e")
            return
        self.info = info
        self._set_name(self.suggested or info["filename"])
        self.size_lbl.config(text=f"Size: {human_size(info['size'])}")
        self.res_lbl.config(text="Resume capability: " + (
            "Yes - multi-connection download" if info["resumable"] else
            "No - single connection, cannot be paused safely"))

    def browse(self):
        p = filedialog.asksaveasfilename(parent=self, initialfile=os.path.basename(self.path.get()),
                                         initialdir=os.path.dirname(self.path.get()))
        if p:
            self.path.set(os.path.normpath(p))

    def go(self, start):
        p = self.path.get().strip()
        name = os.path.basename(p)
        if not name or name == "…":
            messagebox.showwarning(APP_NAME, "Please choose a file name.", parent=self)
            return
        d = self.app.mgr.add(self.url, filename=name, save_dir=os.path.dirname(p),
                             start=start, info=self.info, description=self.description,
                             headers=self.headers)
        self.destroy()
        self.app.refresh(force=True)
        if start and self.app.mgr.settings.get("show_progress_dialog"):
            self.app.show_progress(d)


# ===================================================================
class ProgressDialog(tk.Toplevel):
    """Per-download progress window with a connection map."""

    def __init__(self, app, dl):
        super().__init__(app.root)
        self.app = app
        self.d = dl
        self.details = True
        self.resizable(False, False)
        set_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.close)

        top = ttk.Frame(self, padding=(14, 12, 14, 4))
        top.pack(fill="x")
        self.url_lbl = ttk.Label(top, text="", foreground="#555", width=72)
        self.url_lbl.pack(anchor="w")
        self.status_lbl = ttk.Label(top, text="", font=("Segoe UI", 10, "bold"))
        self.status_lbl.pack(anchor="w", pady=(4, 8))

        grid = ttk.Frame(top)
        grid.pack(fill="x")
        self.fields = {}
        for i, k in enumerate(["File size", "Downloaded", "Transfer rate", "Time left",
                               "Resume capability", "Connections"]):
            ttk.Label(grid, text=k + ":").grid(row=i, column=0, sticky="w", pady=1)
            lbl = ttk.Label(grid, text="")
            lbl.grid(row=i, column=1, sticky="w", padx=10)
            self.fields[k] = lbl

        self.bar = ttk.Progressbar(top, length=520, maximum=1000, style="Big.Horizontal.TProgressbar")
        self.bar.pack(fill="x", pady=(10, 4))

        bf = ttk.Frame(top)
        bf.pack(fill="x", pady=(4, 8))
        self.det_btn = ttk.Button(bf, text="<< Hide details", command=self.toggle_details)
        self.det_btn.pack(side="left")
        ttk.Button(bf, text="Cancel", command=self.cancel).pack(side="right")
        self.pause_btn = ttk.Button(bf, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side="right", padx=6)

        self.det = ttk.Frame(self, padding=(14, 0, 14, 12))
        self.det.pack(fill="both")
        ttk.Label(self.det, text="Start positions and download progress by connections:").pack(
            anchor="w")
        self.canvas = tk.Canvas(self.det, height=22, bg="#e9edf3", highlightthickness=1,
                                highlightbackground="#c8ced8")
        self.canvas.pack(fill="x", pady=(4, 8))
        cols = ("n", "downloaded", "info")
        self.tree = ttk.Treeview(self.det, columns=cols, show="headings", height=6)
        for c, t, w in (("n", "N.", 40), ("downloaded", "Downloaded", 110),
                        ("info", "Info", 360)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both")
        self._update()
        center(self, app.root)

    def toggle_details(self):
        self.details = not self.details
        if self.details:
            self.det.pack(fill="both")
            self.det_btn.config(text="<< Hide details")
        else:
            self.det.pack_forget()
            self.det_btn.config(text="Show details >>")

    def toggle_pause(self):
        if self.d.status in ACTIVE_STATES or self.d.status == QUEUED:
            self.app.mgr.pause(self.d)
        else:
            self.app.mgr.resume(self.d, force=True)

    def cancel(self):
        if self.d.status in ACTIVE_STATES:
            self.app.mgr.pause(self.d)
        self.close()

    def close(self):
        self.app.progress_windows.pop(self.d.id, None)
        self.destroy()

    def _update(self):
        if not self.winfo_exists():
            return
        d = self.d
        pct = d.progress * 100
        self.title(f"{pct:.0f}% {d.filename or 'Connecting…'}")
        self.url_lbl.config(text=d.url if len(d.url) < 90 else d.url[:87] + "…")
        status = d.status
        if status == ERROR:
            status = f"Error: {d.error}"
        elif status == "Downloading":
            status = "Receiving data…"
        self.status_lbl.config(text=status,
                               foreground="#cf222e" if d.status == ERROR else "#1f2328")
        self.fields["File size"].config(text=human_size(d.size))
        self.fields["Downloaded"].config(text=f"{human_size(d.downloaded)}  ({pct:.2f} %)")
        self.fields["Transfer rate"].config(text=human_speed(d.speed) or "-")
        self.fields["Time left"].config(text=human_time(d.eta) or "-")
        self.fields["Resume capability"].config(text="Yes" if d.resumable else "No")
        self.fields["Connections"].config(text=f"{d.active_conns} active / {d.connections} max")
        self.bar["value"] = d.progress * 1000
        active = d.status in ACTIVE_STATES or d.status == QUEUED
        self.pause_btn.config(text="Pause" if active else "Resume",
                              state="disabled" if d.status == COMPLETE else "normal")
        if self.details:
            self._draw_segments()
        if d.status == COMPLETE:
            self.close()
            return
        self.after(400, self._update)

    def _draw_segments(self):
        d = self.d
        c = self.canvas
        c.delete("all")
        w = max(10, c.winfo_width() - 2)
        h = 20
        with d.lock:
            segs = [list(s) for s in d.segments]
        size = d.size if d.size > 0 else 0
        if size:
            for i, (s, e, p) in enumerate(segs):
                x0 = 1 + s / size * w
                x1 = 1 + min(p, e + 1) / size * w
                if x1 > x0:
                    c.create_rectangle(x0, 2, x1, h, fill=SEG_COLORS[i % len(SEG_COLORS)],
                                       width=0)
                c.create_line(x0, 0, x0, h + 2, fill="#57606a")
        rows = []
        if segs:
            for i, (s, e, p) in enumerate(segs):
                total = e - s + 1
                rows.append((i + 1, human_size(p - s),
                             "Done" if p > e else f"{(p - s) / max(1, total) * 100:.0f}% of "
                                                  f"{human_size(total)}"))
        else:
            rows.append((1, human_size(d.downloaded), d.conn_info.get(0, "")))
        existing = self.tree.get_children()
        for i, vals in enumerate(rows):
            if i < len(existing):
                self.tree.item(existing[i], values=vals)
            else:
                self.tree.insert("", "end", values=vals)
        for iid in existing[len(rows):]:
            self.tree.delete(iid)


# ===================================================================
class OptionsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        s = app.mgr.settings
        self.title("Options")
        self.resizable(False, False)
        self.transient(app.root)
        set_icon(self)
        nb = ttk.Notebook(self, padding=8)
        nb.pack(fill="both", expand=True)

        # --- General
        g = ttk.Frame(nb, padding=12)
        nb.add(g, text="General")
        self.clip = tk.BooleanVar(value=s["monitor_clipboard"])
        ttk.Checkbutton(g, text="Watch clipboard for download links", variable=self.clip).pack(
            anchor="w", pady=2)
        self.prog = tk.BooleanVar(value=s["show_progress_dialog"])
        ttk.Checkbutton(g, text="Show download progress window when a download starts",
                        variable=self.prog).pack(anchor="w", pady=2)
        self.comp = tk.BooleanVar(value=s["show_complete_dialog"])
        ttk.Checkbutton(g, text="Show a notification when a download completes",
                        variable=self.comp).pack(anchor="w", pady=2)

        # --- Save to
        sv = ttk.Frame(nb, padding=12)
        nb.add(sv, text="Save To")
        ttk.Label(sv, text="Default download folder:").grid(row=0, column=0, sticky="w")
        self.dir = tk.StringVar(value=s["save_dir"])
        ttk.Entry(sv, textvariable=self.dir, width=50).grid(row=1, column=0, sticky="we")
        ttk.Button(sv, text="Browse…", command=self.browse).grid(row=1, column=1, padx=4)
        self.sort = tk.BooleanVar(value=s["sort_by_category"])
        ttk.Checkbutton(sv, text="Sort files into category sub-folders "
                                 "(Video, Music, Programs…)", variable=self.sort).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=8)

        # --- Connection
        c = ttk.Frame(nb, padding=12)
        nb.add(c, text="Connection")
        ttk.Label(c, text="Max. connections per download:").grid(row=0, column=0, sticky="w",
                                                                  pady=4)
        self.conns = tk.IntVar(value=s["connections"])
        ttk.Combobox(c, textvariable=self.conns, values=[1, 2, 4, 8, 16, 24, 32], width=6,
                     state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(c, text="Max. simultaneous downloads:").grid(row=1, column=0, sticky="w",
                                                                pady=4)
        self.active = tk.IntVar(value=s["max_active"])
        ttk.Spinbox(c, from_=1, to=20, textvariable=self.active, width=6).grid(
            row=1, column=1, sticky="w")
        ttk.Label(c, text="Speed limit (KB/s, 0 = unlimited):").grid(row=2, column=0,
                                                                      sticky="w", pady=4)
        self.limit = tk.IntVar(value=s["speed_limit_kb"])
        ttk.Spinbox(c, from_=0, to=10_000_000, increment=100, textvariable=self.limit,
                    width=10).grid(row=2, column=1, sticky="w")

        # --- Browser
        b = ttk.Frame(nb, padding=12)
        nb.add(b, text="Browser")
        self.bint = tk.BooleanVar(value=s.get("browser_integration", True))
        ttk.Checkbutton(b, text="Take over downloads from the browser extension",
                        variable=self.bint).pack(anchor="w", pady=2)
        self.bdlg = tk.BooleanVar(value=s.get("browser_show_dialog", True))
        ttk.Checkbutton(b, text="Show the “Download File Info” window for browser downloads\n"
                               "(untick to start them immediately)",
                        variable=self.bdlg).pack(anchor="w", pady=2)
        ttk.Button(b, text="Install browser extension…",
                   command=lambda: BrowserSetupDialog(app)).pack(anchor="w", pady=(10, 0))

        bf = ttk.Frame(self, padding=(8, 0, 8, 8))
        bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bf, text="OK", style="Accent.TButton", command=self.ok).pack(side="right",
                                                                                padx=6)
        center(self, app.root)
        make_modal(self)

    def browse(self):
        p = filedialog.askdirectory(parent=self, initialdir=self.dir.get())
        if p:
            self.dir.set(os.path.normpath(p))

    def ok(self):
        s = self.app.mgr.settings
        try:
            s.update(save_dir=self.dir.get().strip() or engine.DEFAULT_SETTINGS["save_dir"],
                     sort_by_category=self.sort.get(), connections=int(self.conns.get()),
                     max_active=max(1, int(self.active.get())),
                     speed_limit_kb=max(0, int(self.limit.get())),
                     monitor_clipboard=self.clip.get(), show_progress_dialog=self.prog.get(),
                     show_complete_dialog=self.comp.get(),
                     browser_integration=self.bint.get(),
                     browser_show_dialog=self.bdlg.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning(APP_NAME, "Please enter valid numbers.", parent=self)
            return
        self.app.mgr.save_settings()
        self.app.mgr.schedule()
        self.destroy()


# ===================================================================
class CompleteDialog(tk.Toplevel):
    def __init__(self, app, d):
        super().__init__(app.root)
        self.title("Download complete")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        set_icon(self)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both")
        ttk.Label(f, text="Download complete", font=("Segoe UI", 12, "bold"),
                  foreground=ACCENT).pack(anchor="w")
        ttk.Label(f, text=d.filename).pack(anchor="w", pady=(6, 0))
        ttk.Label(f, text=f"{human_size(d.size)}   •   {d.save_dir}", foreground="#555").pack(
            anchor="w", pady=(0, 12))
        bf = ttk.Frame(f)
        bf.pack(fill="x")
        ttk.Button(bf, text="Open", style="Accent.TButton",
                   command=lambda: (open_path(d.path), self.destroy())).pack(side="left")
        ttk.Button(bf, text="Open folder",
                   command=lambda: (open_folder(d.path), self.destroy())).pack(side="left",
                                                                                padx=6)
        ttk.Button(bf, text="Close", command=self.destroy).pack(side="right")
        center(self, app.root)


# ===================================================================
class BatchDialog(tk.Toplevel):
    """Pick which of the links sent from the browser to download."""

    def __init__(self, app, items, headers):
        super().__init__(app.root)
        self.app = app
        self.items = items
        self.headers = headers
        self.title(f"SwiftGet - {len(items)} links from browser")
        self.geometry("760x460")
        self.transient(app.root)
        set_icon(self)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Select the files to download (click a row to tick / untick):").pack(
            anchor="w", pady=(0, 6))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("on", "name", "type", "url"), show="headings")
        for c, t, w in (("on", "✔", 34), ("name", "File name", 230), ("type", "Type", 90),
                        ("url", "Address", 360)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c == "on" else "w",
                             stretch=c in ("name", "url"))
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.checked = set()
        for i, it in enumerate(items):
            name = it.get("filename") or engine.sanitize_filename(
                os.path.basename(it["url"].split("?")[0])) or it["url"]
            it["_name"] = name
            self.tree.insert("", "end", iid=str(i),
                             values=("☑", name, engine.category_for(name), it["url"]))
            self.checked.add(str(i))
        self.tree.bind("<Button-1>", self._toggle)
        self.tree.bind("<space>", self._toggle)

        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(10, 0))
        ttk.Button(bf, text="Check all", command=lambda: self._all(True)).pack(side="left")
        ttk.Button(bf, text="Uncheck all", command=lambda: self._all(False)).pack(side="left",
                                                                                padx=6)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bf, text="Add paused", command=lambda: self.go(False)).pack(side="right",
                                                                              padx=6)
        ttk.Button(bf, text="Download", style="Accent.TButton",
                   command=lambda: self.go(True)).pack(side="right")
        center(self, app.root)
        make_modal(self)

    def _toggle(self, e):
        iid = self.tree.identify_row(e.y) if e.type == tk.EventType.ButtonPress else \
            self.tree.focus()
        if not iid:
            return
        if iid in self.checked:
            self.checked.discard(iid)
            self.tree.set(iid, "on", "☐")
        else:
            self.checked.add(iid)
            self.tree.set(iid, "on", "☑")

    def _all(self, on):
        for iid in self.tree.get_children():
            if on:
                self.checked.add(iid)
            else:
                self.checked.discard(iid)
            self.tree.set(iid, "on", "☑" if on else "☐")

    def go(self, start):
        for iid in sorted(self.checked, key=int):
            it = self.items[int(iid)]
            self.app.mgr.add(it["url"], filename=it.get("filename") or None, start=start,
                             headers=self.headers, description=it.get("text", "")[:120])
        self.destroy()
        self.app.refresh(force=True)


# ===================================================================
class BrowserSetupDialog(tk.Toplevel):
    """Explains and installs the browser extension."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Browser integration")
        self.resizable(False, False)
        self.transient(app.root)
        set_icon(self)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Browser integration", font=("Segoe UI", 12, "bold"),
                  foreground=ACCENT).pack(anchor="w")
        ok = app.bridge.server is not None
        ttk.Label(f, text=("● SwiftGet is listening for the browser extension on "
                           f"127.0.0.1:{bridge.PORT}") if ok else
                  f"● Not listening: {app.bridge.error}",
                  foreground="#2da44e" if ok else "#cf222e").pack(anchor="w", pady=(4, 10))
        steps = (
            "Install the SwiftGet extension once in Chrome, Edge, Brave, Opera or Vivaldi:\n\n"
            "  1.  Click “Copy extension folder” below. A folder window opens.\n"
            "  2.  Open your browser's extensions page (buttons below), or type\n"
            "       chrome://extensions  /  edge://extensions  in the address bar.\n"
            "  3.  Turn on “Developer mode” (top-right in Chrome, left side in Edge).\n"
            "  4.  Click “Load unpacked” and choose the folder that opened in step 1.\n\n"
            "After that, file downloads in the browser go to SwiftGet automatically.\n"
            "Right-click a link or video and choose “Download with SwiftGet”, or use\n"
            "the extension's toolbar button to grab every link on a page.\n\n"
            "If SwiftGet is closed, the browser downloads the file normally.")
        ttk.Label(f, text=steps, justify="left").pack(anchor="w")
        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(14, 0))
        ttk.Button(bf, text="Copy extension folder", style="Accent.TButton",
                   command=self.export).pack(side="left")
        ttk.Button(bf, text="Open Chrome extensions",
                   command=lambda: self.open_browser("chrome", "chrome://extensions")).pack(
            side="left", padx=6)
        ttk.Button(bf, text="Open Edge extensions",
                   command=lambda: self.open_browser("msedge", "edge://extensions")).pack(
            side="left")
        ttk.Button(bf, text="Close", command=self.destroy).pack(side="right")
        center(self, app.root)

    def export(self):
        import shutil
        src = resource("browser-extension")
        if not os.path.isdir(src):  # running from source checkout
            src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension")
        dst = os.path.join(data_dir(), "browser-extension")
        try:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("dist", "*.py"))
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not copy the extension:\n{e}", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(dst)
        self.app._last_clip = dst
        open_path(dst)
        messagebox.showinfo(APP_NAME, f"Extension folder ready (path copied to clipboard):\n\n"
                                      f"{dst}\n\nNow choose this folder with “Load unpacked”.",
                            parent=self)

    def open_browser(self, exe, url):
        try:
            if sys.platform == "win32":
                subprocess.Popen(["cmd", "/c", "start", "", exe, url],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.Popen([{"msedge": "microsoft-edge"}.get(exe, "google-chrome"), url])
        except OSError:
            messagebox.showinfo(APP_NAME, f"Open your browser and go to:\n{url}", parent=self)


# ===================================================================
class App:
    COLS = (("name", "File Name", 300), ("size", "Size", 90), ("status", "Status", 110),
            ("eta", "Time left", 80), ("rate", "Transfer rate", 100),
            ("last", "Last Try Date", 130), ("desc", "Description", 180))

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1100x620")
        self.root.minsize(760, 400)
        set_icon(self.root)
        self.mgr = engine.DownloadManager(data_dir())
        self.bridge = bridge.Bridge(VERSION, lambda: self.mgr.settings.get(
            "browser_integration", True))
        self.bridge.start()
        self.progress_windows: dict[str, ProgressDialog] = {}
        self._recent: dict[str, float] = {}
        self.filter = "All Downloads"
        self.sort_col, self.sort_rev = None, False
        self._last_clip = ""
        self._init_clip()
        self._style()
        self._menu()
        self._toolbar()
        self._body()
        self._statusbar()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<Control-n>", lambda e: self.add_url())
        self.root.bind("<Delete>", lambda e: self.delete())
        self.refresh(force=True)
        self._tick()
        self.mgr.schedule()

    # ---------------------------------------------------------- style
    def _style(self):
        st = ttk.Style(self.root)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        elif "clam" in st.theme_names():
            st.theme_use("clam")
        self.root.configure(bg=BG)
        st.configure("Accent.TButton", foreground=ACCENT_DARK, font=("Segoe UI", 9, "bold"))
        st.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", font=("Segoe UI", 9))
        st.configure("Big.Horizontal.TProgressbar", thickness=18)
        st.configure("Side.Treeview", rowheight=26, font=("Segoe UI", 9))

    def _menu(self):
        m = tk.Menu(self.root)
        tasks = tk.Menu(m, tearoff=0)
        tasks.add_command(label="Add new download…", accelerator="Ctrl+N", command=self.add_url)
        tasks.add_command(label="Add batch download from clipboard…", command=self.add_batch)
        tasks.add_command(label="Browser integration…",
                          command=lambda: BrowserSetupDialog(self))
        tasks.add_separator()
        tasks.add_command(label="Exit", command=self.quit)
        m.add_cascade(label="Tasks", menu=tasks)

        files = tk.Menu(m, tearoff=0)
        files.add_command(label="Open", command=lambda: self._each(lambda d: open_path(d.path)))
        files.add_command(label="Open folder",
                          command=lambda: self._each(lambda d: open_folder(d.path)))
        files.add_separator()
        files.add_command(label="Delete", command=self.delete)
        files.add_command(label="Delete completed", command=self.delete_completed)
        m.add_cascade(label="File", menu=files)

        dl = tk.Menu(m, tearoff=0)
        dl.add_command(label="Resume", command=self.resume)
        dl.add_command(label="Pause", command=self.pause)
        dl.add_command(label="Pause all", command=self.mgr.pause_all)
        dl.add_command(label="Resume all", command=self.mgr.resume_all)
        dl.add_command(label="Redownload", command=self.redownload)
        dl.add_separator()
        self.queue_var = tk.BooleanVar(value=True)
        dl.add_checkbutton(label="Process queue", variable=self.queue_var,
                           command=self.toggle_queue)
        dl.add_separator()
        dl.add_command(label="Options…", command=lambda: OptionsDialog(self))
        m.add_cascade(label="Downloads", menu=dl)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="About SwiftGet", command=self.about)
        m.add_cascade(label="Help", menu=hm)
        self.root.config(menu=m)

    def _toolbar(self):
        bar = tk.Frame(self.root, bg=PANEL, bd=0, highlightthickness=1,
                       highlightbackground="#d8dee8")
        bar.pack(fill="x")
        buttons = [
            ("＋", "Add URL", self.add_url, ACCENT),
            ("▶", "Resume", self.resume, "#2da44e"),
            ("❚❚", "Pause", self.pause, "#bf8700"),
            ("■", "Pause All", self.mgr.pause_all, "#cf222e"),
            ("✕", "Delete", self.delete, "#57606a"),
            ("✔", "Delete Completed", self.delete_completed, "#57606a"),
            ("⚙", "Options", lambda: OptionsDialog(self), "#57606a"),
            ("🌐", "Browser", lambda: BrowserSetupDialog(self), "#0598bc"),
            ("⏵⏵", "Start Queue", lambda: self._set_queue(True), "#0598bc"),
            ("⏸", "Stop Queue", lambda: self._set_queue(False), "#8250df"),
        ]
        for icon, label, cmd, color in buttons:
            b = tk.Frame(bar, bg=PANEL, cursor="hand2", padx=10, pady=6)
            b.pack(side="left")
            i = tk.Label(b, text=icon, fg=color, bg=PANEL, font=("Segoe UI Symbol", 17))
            i.pack()
            t = tk.Label(b, text=label, bg=PANEL, fg="#1f2328", font=("Segoe UI", 8))
            t.pack()
            for w in (b, i, t):
                w.bind("<Button-1>", lambda e, c=cmd: c())
                w.bind("<Enter>", lambda e, f=b: self._hover(f, True))
                w.bind("<Leave>", lambda e, f=b: self._hover(f, False))

    @staticmethod
    def _hover(frame, on):
        c = "#e6eefc" if on else PANEL
        frame.config(bg=c)
        for ch in frame.winfo_children():
            ch.config(bg=c)

    def _body(self):
        pw = ttk.PanedWindow(self.root, orient="horizontal")
        pw.pack(fill="both", expand=True)

        left = ttk.Frame(pw)
        ttk.Label(left, text="Categories", font=("Segoe UI", 9, "bold"), padding=(8, 6)).pack(
            anchor="w")
        self.side = ttk.Treeview(left, show="tree", style="Side.Treeview", selectmode="browse")
        self.side.pack(fill="both", expand=True)
        root_id = self.side.insert("", "end", "All Downloads", text="📁 All Downloads", open=True)
        icons = {"General": "📄", "Compressed": "🗜", "Documents": "📝", "Music": "🎵",
                 "Programs": "💿", "Video": "🎬", "Images": "🖼"}
        for cat in ["General"] + list(engine.CATEGORIES):
            self.side.insert(root_id, "end", cat, text=f"{icons.get(cat, '📄')} {cat}")
        self.side.insert("", "end", "Unfinished", text="⏳ Unfinished")
        self.side.insert("", "end", "Finished", text="✅ Finished")
        self.side.insert("", "end", "Queued", text="🕑 Queued")
        self.side.selection_set("All Downloads")
        self.side.bind("<<TreeviewSelect>>", self._on_filter)
        pw.add(left, weight=0)

        right = ttk.Frame(pw)
        cols = [c[0] for c in self.COLS]
        self.tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        for key, title, w in self.COLS:
            self.tree.heading(key, text=title, command=lambda k=key: self._sort(k))
            self.tree.column(key, width=w, anchor="e" if key in ("size", "rate") else "w",
                             stretch=key in ("name", "desc"))
        vs = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("complete", foreground="#57606a")
        self.tree.tag_configure("error", foreground="#cf222e")
        self.tree.tag_configure("active", foreground=ACCENT_DARK)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-3>", self._on_context)
        self.tree.bind("<Return>", self._on_double)
        pw.add(right, weight=1)

        self.ctx = tk.Menu(self.root, tearoff=0)
        self.ctx.add_command(label="Open", command=lambda: self._each(lambda d: open_path(d.path)))
        self.ctx.add_command(label="Open folder",
                             command=lambda: self._each(lambda d: open_folder(d.path)))
        self.ctx.add_command(label="Show progress",
                             command=lambda: self._each(self.show_progress))
        self.ctx.add_separator()
        self.ctx.add_command(label="Resume", command=self.resume)
        self.ctx.add_command(label="Pause", command=self.pause)
        self.ctx.add_command(label="Redownload", command=self.redownload)
        self.ctx.add_separator()
        self.ctx.add_command(label="Copy address", command=self.copy_url)
        self.ctx.add_command(label="Delete", command=self.delete)
        self.ctx.add_command(label="Properties", command=self.properties)

    def _statusbar(self):
        self.status = tk.StringVar()
        bar = tk.Frame(self.root, bg="#e9edf3")
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status, bg="#e9edf3", fg="#1f2328",
                 font=("Segoe UI", 8), anchor="w", padx=8, pady=3).pack(fill="x")

    # ---------------------------------------------------------- data
    def _visible(self):
        f = self.filter
        out = []
        for d in list(self.mgr.downloads):
            if f == "All Downloads":
                ok = True
            elif f == "Unfinished":
                ok = d.status != COMPLETE
            elif f == "Finished":
                ok = d.status == COMPLETE
            elif f == "Queued":
                ok = d.status == QUEUED
            else:
                ok = d.category == f
            if ok:
                out.append(d)
        if self.sort_col:
            key = {
                "name": lambda d: d.filename.lower(), "size": lambda d: d.size,
                "status": lambda d: d.progress, "eta": lambda d: d.eta or 0,
                "rate": lambda d: d.speed, "last": lambda d: d.last_try or 0,
                "desc": lambda d: d.description.lower(),
            }[self.sort_col]
            out.sort(key=key, reverse=self.sort_rev)
        return out

    @staticmethod
    def _row(d):
        if d.status == COMPLETE:
            status = "Complete"
        elif d.status in ACTIVE_STATES and d.size > 0:
            status = f"{d.progress * 100:.2f}%"
        elif d.status == ERROR:
            status = "Error"
        elif d.status == PAUSED and d.progress > 0:
            status = f"Paused {d.progress * 100:.0f}%"
        else:
            status = d.status
        last = time.strftime("%b %d %H:%M", time.localtime(d.last_try)) if d.last_try else ""
        active = d.status in ACTIVE_STATES
        return (d.filename or d.url, human_size(d.size), status,
                human_time(d.eta) if active else "", human_speed(d.speed) if active else "",
                last, d.error if d.status == ERROR else d.description)

    def refresh(self, force=False):
        vis = self._visible()
        ids = [d.id for d in vis]
        existing = list(self.tree.get_children())
        if force or existing != ids:
            sel = set(self.tree.selection())
            self.tree.delete(*existing)
            for d in vis:
                self.tree.insert("", "end", iid=d.id, values=self._row(d), tags=self._tag(d))
            keep = [i for i in sel if i in ids]
            if keep:
                self.tree.selection_set(keep)
        else:
            for d in vis:
                self.tree.item(d.id, values=self._row(d), tags=self._tag(d))
        active = [d for d in self.mgr.downloads if d.status in ACTIVE_STATES]
        total_speed = sum(d.speed for d in active)
        lim = self.mgr.settings.get("speed_limit_kb", 0)
        self.status.set(
            f"  {len(self.mgr.downloads)} downloads   •   {len(active)} active   •   "
            f"{human_speed(total_speed) or '0 B/s'}"
            + (f"   •   limit {lim} KB/s" if lim else "")
            + ("" if self.mgr.queue_running else "   •   queue stopped")
            + ("   •   browser: " + ("connected" if self.bridge.server and
                                     self.mgr.settings.get("browser_integration", True)
                                     else "off")))

    @staticmethod
    def _tag(d):
        if d.status == COMPLETE:
            return ("complete",)
        if d.status == ERROR:
            return ("error",)
        if d.status in ACTIVE_STATES:
            return ("active",)
        return ()

    def _tick(self):
        try:
            self.refresh()
            while self.mgr.just_completed:
                d = self.mgr.just_completed.pop(0)
                if self.mgr.settings.get("show_complete_dialog"):
                    CompleteDialog(self, d)
            self._check_clipboard()
            self._browser_events()
        finally:
            self.root.after(500, self._tick)

    # ---------------------------------------------------------- browser
    def bring_to_front(self):
        """Restore the window even if it is minimized or behind others."""
        r = self.root
        try:
            if r.state() in ("iconic", "withdrawn"):
                r.state("normal")
            r.deiconify()
            if sys.platform == "win32":
                import ctypes
                hwnd = int(r.wm_frame(), 16)
                user32 = ctypes.windll.user32
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            r.lift()
            r.attributes("-topmost", True)
            r.after(300, lambda: r.attributes("-topmost", False))
            r.focus_force()
            # dialogs belonging to the window come back with it
            for w in r.winfo_children():
                if isinstance(w, tk.Toplevel) and w.winfo_exists():
                    w.deiconify()
                    w.lift()
        except (tk.TclError, AttributeError, OSError, ValueError):
            pass

    def _offer_once(self, url):
        """True the first time a URL is offered; False if it was offered in the
        last 20 s or is already being shown (stops duplicate pop-ups)."""
        now = time.monotonic()
        self._recent = {u: t for u, t in self._recent.items() if now - t < 20}
        url = url.strip()
        if url in self._recent:
            return False
        if any(isinstance(w, FileInfoDialog) and w.url == url
               for w in self.root.winfo_children()):
            return False
        self._recent[url] = now
        return True

    def _browser_events(self):
        # "show" is always handled at once; new downloads wait until any
        # open dialog is closed so they never stack on top of each other
        q = self.bridge.events
        pending = []
        while not q.empty():
            kind, data = q.get_nowait()
            if kind == "show":
                self.bring_to_front()
            else:
                pending.append((kind, data))
        for ev in pending:
            q.put(ev)
        if self._modal_open() or not pending:
            return
        kind, data = q.get_nowait()
        if kind == "add" and not self._offer_once(data["url"]):
            return
        if kind == "add":
            if self.mgr.settings.get("browser_show_dialog", True):
                self.bring_to_front()
                FileInfoDialog(self, data["url"], headers=data["headers"],
                               filename=data["filename"], size=data["size"])
            else:
                d = self.mgr.add(data["url"], filename=data["filename"] or None,
                                 headers=data["headers"])
                self.refresh(force=True)
                if self.mgr.settings.get("show_progress_dialog"):
                    self.show_progress(d)
        elif kind == "batch":
            self.bring_to_front()
            BatchDialog(self, data["items"], data["headers"])

    # ---------------------------------------------------------- clipboard
    def _init_clip(self):
        try:
            self._last_clip = self.root.clipboard_get()
        except tk.TclError:
            self._last_clip = ""

    def _check_clipboard(self):
        if not self.mgr.settings.get("monitor_clipboard"):
            return
        try:
            t = self.root.clipboard_get()
        except tk.TclError:
            return
        if t == self._last_clip:
            return
        self._last_clip = t
        if engine.looks_like_download_url(t) and not self._modal_open():
            if not any(d.url == t.strip() for d in self.mgr.downloads) and \
                    self._offer_once(t):
                self.bring_to_front()
                FileInfoDialog(self, t.strip())

    def _modal_open(self):
        return any(isinstance(w, (FileInfoDialog, AddUrlDialog, OptionsDialog, BatchDialog))
                   for w in self.root.winfo_children())

    # ---------------------------------------------------------- actions
    def _selected(self):
        return [d for d in (self.mgr.get(i) for i in self.tree.selection()) if d]

    def _each(self, fn):
        for d in self._selected():
            fn(d)

    def add_url(self):
        AddUrlDialog(self)

    def add_batch(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        urls = [u.strip() for u in text.split() if u.strip().lower().startswith(("http://",
                                                                                 "https://"))]
        if not urls:
            messagebox.showinfo(APP_NAME, "No links found in the clipboard.\n\n"
                                          "Copy a list of addresses (one per line) first.")
            return
        if messagebox.askyesno(APP_NAME, f"Add {len(urls)} links to the queue?"):
            for u in urls:
                self.mgr.add(u)
            self.refresh(force=True)

    def resume(self):
        for d in self._selected():
            self.mgr.resume(d, force=True)
            if self.mgr.settings.get("show_progress_dialog"):
                self.show_progress(d)
        self.refresh()

    def pause(self):
        self._each(self.mgr.pause)
        self.refresh()

    def redownload(self):
        sel = self._selected()
        if sel and messagebox.askyesno(APP_NAME, "Start the selected downloads over again?"):
            for d in sel:
                self.mgr.redownload(d)
        self.refresh()

    def delete(self):
        sel = self._selected()
        if not sel:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("Confirm deletion")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        f = ttk.Frame(dlg, padding=16)
        f.pack()
        ttk.Label(f, text=f"Delete {len(sel)} item(s) from the list?").pack(anchor="w")
        also = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Also delete downloaded files from disk",
                        variable=also).pack(anchor="w", pady=8)

        def ok():
            for d in sel:
                w = self.progress_windows.get(d.id)
                if w:
                    w.close()
                self.mgr.remove(d, delete_file=also.get())
            dlg.destroy()
            self.refresh(force=True)

        bf = ttk.Frame(f)
        bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right")
        ttk.Button(bf, text="Delete", style="Accent.TButton", command=ok).pack(side="right",
                                                                              padx=6)
        center(dlg, self.root)
        make_modal(dlg)

    def delete_completed(self):
        if messagebox.askyesno(APP_NAME, "Remove all completed downloads from the list?\n"
                                         "(Files stay on disk.)"):
            self.mgr.remove_completed()
            self.refresh(force=True)

    def copy_url(self):
        sel = self._selected()
        if sel:
            self.root.clipboard_clear()
            text = "\n".join(d.url for d in sel)
            self.root.clipboard_append(text)
            self._last_clip = text

    def properties(self):
        for d in self._selected()[:1]:
            messagebox.showinfo(
                "Properties",
                f"File: {d.filename}\nFolder: {d.save_dir}\nURL: {d.url}\n\n"
                f"Size: {human_size(d.size)}\nDownloaded: {human_size(d.downloaded)}\n"
                f"Status: {d.status}\nResume capability: {'Yes' if d.resumable else 'No'}\n"
                f"Connections: {d.connections}\nCategory: {d.category}\n"
                f"Added: {time.ctime(d.added)}"
                + (f"\nCompleted: {time.ctime(d.completed_at)}" if d.completed_at else "")
                + (f"\nError: {d.error}" if d.error else ""))

    def show_progress(self, d):
        w = self.progress_windows.get(d.id)
        if w and w.winfo_exists():
            w.lift()
            return
        if d.status == COMPLETE:
            return
        self.progress_windows[d.id] = ProgressDialog(self, d)

    def toggle_queue(self):
        self._set_queue(self.queue_var.get())

    def _set_queue(self, on):
        self.mgr.queue_running = on
        self.queue_var.set(on)
        if on:
            self.mgr.schedule()
        self.refresh()

    def _on_filter(self, _e=None):
        sel = self.side.selection()
        if sel:
            self.filter = sel[0]
            self.refresh(force=True)

    def _sort(self, col):
        if self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col, self.sort_rev = col, False
        self.refresh(force=True)

    def _on_double(self, _e=None):
        sel = self._selected()
        if not sel:
            return
        d = sel[0]
        if d.status == COMPLETE:
            if os.path.exists(d.path):
                open_path(d.path)
            else:
                messagebox.showwarning(APP_NAME, f"The file no longer exists:\n{d.path}")
        else:
            if d.status in (PAUSED, ERROR):
                self.mgr.resume(d, force=True)
            self.show_progress(d)

    def _on_context(self, e):
        iid = self.tree.identify_row(e.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.tree.selection():
            self.ctx.tk_popup(e.x_root, e.y_root)

    def about(self):
        messagebox.showinfo(
            "About", f"{APP_NAME} {VERSION}\n\n"
                     "Multi-connection download accelerator with pause/resume,\n"
                     "categories, queue, speed limiter and clipboard monitoring.")

    def quit(self):
        active = [d for d in self.mgr.downloads if d.status in ACTIVE_STATES]
        if active and not messagebox.askyesno(
                APP_NAME, f"{len(active)} download(s) in progress.\n"
                          "They will be paused and can be resumed later. Exit?"):
            return
        self.mgr.shutdown()
        self.bridge.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    if bridge.ping_existing():
        bridge.show_existing()   # already running: bring that window forward
        return
    App().run()


if __name__ == "__main__":
    main()

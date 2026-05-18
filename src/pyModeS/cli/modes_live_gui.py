#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import os
import signal
import webbrowser
import json
import re

IP = "172.22.100.140"
PORT = "30005"
VENV_ACTIVATE = ".venv/bin/activate"
AC_LIST_PATH = "src/pyModeS/cli/AC_List_CH.json"

try:
    with open(AC_LIST_PATH, "r") as f:
        AC_List = json.load(f)
except FileNotFoundError:
    AC_List = {}


class ModesLiveGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ModesLive")
        self.root.resizable(True, False)
        self.process = None

        root.columnconfigure(1, weight=1)

        # ── Dump file ──────────────────────────────────────────────
        tk.Label(root, text="Dump file").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(14, 4))

        dump_frame = tk.Frame(root)
        dump_frame.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=(14, 4))
        dump_frame.columnconfigure(0, weight=1)

        self.dump_entry = tk.Entry(dump_frame)
        self.dump_entry.insert(0, "./dump")
        self.dump_entry.grid(row=0, column=0, sticky="ew")

        self.dump_var = tk.BooleanVar(value=True)
        tk.Checkbutton(dump_frame, text="Enable", variable=self.dump_var,
                       command=self._toggle_dump).grid(row=0, column=1, padx=(6, 0))

        tk.Label(root, text="Leave empty to disable dump", fg="gray",
                 font=("Arial", 9)).grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 6))

        # ── AC filter ──────────────────────────────────────────────
        tk.Label(root, text="AC filter").grid(row=2, column=0, sticky="w", padx=(12, 6), pady=4)

        self.filter_entry = ttk.Combobox(root, values=list(AC_List.keys()))
        self.filter_entry.grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=4)

        tk.Button(root, text="Check", width=7,
                  command=self._check_filter).grid(row=2, column=2, padx=(0, 12), pady=4)

        tk.Label(root, text="Empty = all aircraft", fg="gray",
                 font=("Arial", 9)).grid(row=3, column=1, sticky="w", padx=(0, 12), pady=(0, 6))

        # ── Mode (TUI / Web) ───────────────────────────────────────
        tk.Label(root, text="Mode").grid(row=4, column=0, sticky="w", padx=(12, 6), pady=4)

        mode_frame = tk.Frame(root)
        mode_frame.grid(row=4, column=1, columnspan=2, sticky="w", padx=(0, 12), pady=4)

        self.mode_var = tk.StringVar(value="--tui")
        tk.Radiobutton(mode_frame, text="TUI", variable=self.mode_var,
                       value="--tui").pack(side="left")
        tk.Radiobutton(mode_frame, text="Web dashboard", variable=self.mode_var,
                       value="--web").pack(side="left", padx=(12, 0))

        # ── Buttons ────────────────────────────────────────────────
        btn_frame = tk.Frame(root)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 10))
        btn_frame.columnconfigure((0, 1), weight=1)

        self.btn_start = tk.Button(btn_frame, text="▶  Start", command=self._start,
                                   bg="#2d6a4f", fg="white", activebackground="#1b4332",
                                   activeforeground="white", relief="flat", padx=10, pady=6)
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.btn_stop = tk.Button(btn_frame, text="■  Stop", command=self._stop,
                                  bg="#9d0208", fg="white", activebackground="#6a040f",
                                  activeforeground="white", relief="flat", padx=10, pady=6,
                                  state="disabled")
        self.btn_stop.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # ── Status bar ─────────────────────────────────────────────
        self.status_var = tk.StringVar(value=f"Ready  ·  {IP}:{PORT}")
        status_bar = tk.Label(root, textvariable=self.status_var, anchor="w",
                              relief="sunken", font=("Arial", 9), pady=4, padx=8)
        status_bar.grid(row=6, column=0, columnspan=3, sticky="ew")
        self._status_label = status_bar

    # ── helpers ────────────────────────────────────────────────────

    def _toggle_dump(self):
        state = "normal" if self.dump_var.get() else "disabled"
        self.dump_entry.config(state=state)

    def _resolve_filter(self):
        """Return raw ICAO hex string or empty string."""
        val = self.filter_entry.get().strip()
        if not val:
            return ""
        if val in AC_List:
            return AC_List[val]
        return val

    def _check_filter(self):
        val = self.filter_entry.get().strip()
        if not val:
            messagebox.showinfo("AC filter", "Empty — will capture all aircraft.")
            return
        if val in AC_List:
            icao = AC_List[val]
            messagebox.showinfo("AC filter", f"{val}  →  ICAO {icao}")
        elif re.fullmatch(r"[0-9a-fA-F]{6}", val):
            messagebox.showinfo("AC filter", f"Raw ICAO hex: {val.upper()}")
        else:
            messagebox.showwarning("AC filter",
                                   f'"{val}" not found in database.\n'
                                   "Enter a 6-digit hex ICAO directly.")

    def _set_status(self, text, color="black"):
        self.status_var.set(text)
        self._status_label.config(fg=color)

    # ── start / stop ───────────────────────────────────────────────

    def _start(self):
        if self.process:
            self._set_status("Already running.", "orange")
            return

        dump = None
        if self.dump_var.get():
            name = self.dump_entry.get().strip()
            if name:
                dump = name if name.endswith(".csv") else name + ".csv"

        ac_filter = self._resolve_filter()
        mode = self.mode_var.get()

        cmd = ["modes", "live", "--network", f"{IP}:{PORT}", mode]
        if dump:
            cmd += ["--csv", dump]
        if ac_filter:
            cmd += ["--ac-filter", ac_filter]

        bash_cmd = f"source {VENV_ACTIVATE} && " + " ".join(cmd)

        try:
            self.process = subprocess.Popen(
                ["bash", "-c", bash_cmd],
                preexec_fn=os.setsid,
            )
        except Exception as e:
            messagebox.showerror("Launch failed", str(e))
            return

        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

        status = f"Running  ·  {IP}:{PORT}  ·  {mode}"
        if dump:
            status += f"  ·  {dump}"
        if ac_filter:
            status += f"  ·  filter {ac_filter}"
        self._set_status(status, "green")

        if mode == "--web":
            self.root.after(800, self._offer_dashboard)

    def _offer_dashboard(self):
        if messagebox.askyesno("Dashboard", "Open web dashboard at localhost:8080?"):
            webbrowser.open("http://localhost:8080")

    def _stop(self):
        if not self.process:
            self._set_status("Not running.", "gray")
            return
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        self.process = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._set_status("Stopped.", "red")


if __name__ == "__main__":
    root = tk.Tk()
    app = ModesLiveGUI(root)
    root.mainloop()
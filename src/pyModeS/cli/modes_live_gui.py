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
VENV_ACTIVATE = "/home/pi/pyModeS/.venv/bin/activate"
AC_LIST_PATH = "/home/pi/pyModeS/src/pyModeS/cli/AC_List_CH.json"

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
        self.dump_entry.insert(0, "/home/pi/pyModeS/dump/")
        self.dump_entry.grid(row=0, column=0, sticky="ew")

        self.dump_var = tk.BooleanVar(value=True)
        tk.Checkbutton(dump_frame, text="Enable", variable=self.dump_var,
                       command=self._toggle_dump).grid(row=0, column=1, padx=(6, 0))

        tk.Label(root, text="Leave empty to disable dump", fg="gray",
                 font=("Arial", 9)).grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 6))

        # ── AC filter ──────────────────────────────────────────────
        tk.Label(root, text="AC filter").grid(row=2, column=0, sticky="nw", padx=(12, 6), pady=(6, 0))

        ac_frame = tk.Frame(root)
        ac_frame.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=(6, 0))
        ac_frame.columnconfigure(0, weight=1)

        input_row = tk.Frame(ac_frame)
        input_row.grid(row=0, column=0, sticky="ew")
        input_row.columnconfigure(0, weight=1)

        self.filter_entry = ttk.Combobox(input_row, values=list(AC_List.keys()))
        self.filter_entry.grid(row=0, column=0, sticky="ew")
        tk.Button(input_row, text="Add", command=self._add_filter).grid(row=0, column=1, padx=(6, 0))
        tk.Button(input_row, text="Check", command=self._check_filter).grid(row=0, column=2, padx=(4, 0))

        list_row = tk.Frame(ac_frame)
        list_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        list_row.columnconfigure(0, weight=1)

        self.filter_listbox = tk.Listbox(list_row, height=3, selectmode=tk.SINGLE,
                                         exportselection=False)
        self.filter_listbox.grid(row=0, column=0, sticky="ew")
        tk.Button(list_row, text="Remove", command=self._remove_filter).grid(
            row=0, column=1, padx=(6, 0), sticky="n")

        tk.Label(root, text="Empty list = all aircraft", fg="gray",
                 font=("Arial", 9)).grid(row=3, column=1, sticky="w", padx=(0, 12), pady=(2, 6))

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
        """Return resolved ICAO hex string (upper) from the combobox, or empty string."""
        val = self.filter_entry.get().strip()
        if not val:
            return ""
        if val in AC_List:
            return AC_List[val].upper()
        return val.upper()

    def _add_filter(self):
        icao = self._resolve_filter()
        if not icao:
            messagebox.showwarning("AC filter", "Enter an ICAO address or select from the list.")
            return
        if not re.fullmatch(r"[0-9a-fA-F]{6}", icao):
            messagebox.showwarning("AC filter",
                                   f'"{icao}" is not a valid 6-digit hex ICAO.')
            return
        existing = list(self.filter_listbox.get(0, tk.END))
        if icao not in existing:
            self.filter_listbox.insert(tk.END, icao)
        self.filter_entry.set("")

    def _remove_filter(self):
        sel = self.filter_listbox.curselection()
        if sel:
            self.filter_listbox.delete(sel[0])

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
                dump = name.removesuffix(".csv")
    
        icao_list = list(self.filter_listbox.get(0, tk.END))
        mode = self.mode_var.get()

        if dump and not icao_list:
            if not messagebox.askyesno(
                "No AC filter",
                "Dump is enabled but no AC filter is set.\n\n"
                "This will record all aircraft.\n\n"
                "Start anyway?",
            ):
                return

        cmd = [
            "/home/pi/pyModeS/.venv/bin/modes",
            "live", "--network", f"{IP}:{PORT}", mode
        ]
        if dump:
            cmd += ["--csv", dump]
        for icao in icao_list:
            cmd += ["--ac-filter", icao]
    
        # Set up the venv environment without needing bash/source
        env = os.environ.copy()
        env["VIRTUAL_ENV"] = "/home/pi/pyModeS/.venv"
        env["PATH"] = "/home/pi/pyModeS/.venv/bin:" + env["PATH"]
        env.pop("PYTHONHOME", None)
    
        try:
            self.process = subprocess.Popen(
                cmd,
                env=env,
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
        if icao_list:
            status += f"  ·  filter {','.join(icao_list)}"
        self._set_status(status, "green")

        if mode == "--web":
            self.root.after(800, self._offer_dashboard)

    def _offer_dashboard(self):
        if messagebox.askyesno("Dashboard", "Open web dashboard at localhost:8088?"):
            webbrowser.open("http://localhost:8088")

    def _stop(self):
        if not self.process:
            self._set_status("Not running.", "gray")
            return
        try:
            pgid = os.getpgid(self.process.pid)
            os.killpg(pgid, signal.SIGINT)   # ← SIGINT, not SIGTERM
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)  # force-kill if still alive
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
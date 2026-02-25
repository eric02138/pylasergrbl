"""Jog control panel widget."""

import tkinter as tk
from tkinter import ttk
from typing import Callable


class JogPanel(ttk.LabelFrame):
    STEP_SIZES = [0.1, 0.5, 1.0, 5.0, 10.0, 50.0, 100.0]

    def __init__(self, parent, jog_callback: Callable, **kwargs):
        super().__init__(parent, text="Jog Controls", **kwargs)
        self._jog_callback = jog_callback
        self._step_size = tk.DoubleVar(value=1.0)
        self._feed_rate = tk.IntVar(value=1000)
        self._build_ui()

    def _build_ui(self):
        bf = ttk.Frame(self)
        bf.pack(padx=5, pady=5)
        s = {"width": 4}
        ttk.Button(bf, text="Y+", command=lambda: self._jog(0,1), **s).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(bf, text="X-", command=lambda: self._jog(-1,0), **s).grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(bf, text="H", command=lambda: self._jog(0,0,home=True), **s).grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(bf, text="X+", command=lambda: self._jog(1,0), **s).grid(row=1, column=2, padx=2, pady=2)
        ttk.Button(bf, text="Y-", command=lambda: self._jog(0,-1), **s).grid(row=2, column=1, padx=2, pady=2)
        ttk.Button(bf, text="Z+", command=lambda: self._jog(0,0,z=1), **s).grid(row=0, column=3, padx=8, pady=2)
        ttk.Button(bf, text="Z-", command=lambda: self._jog(0,0,z=-1), **s).grid(row=2, column=3, padx=8, pady=2)

        sf = ttk.Frame(self)
        sf.pack(padx=5, pady=2, fill="x")
        ttk.Label(sf, text="Step (mm):").pack(side="left")
        ttk.Combobox(sf, textvariable=self._step_size, values=self.STEP_SIZES, width=8).pack(side="left", padx=5)

        ff = ttk.Frame(self)
        ff.pack(padx=5, pady=2, fill="x")
        ttk.Label(ff, text="Feed (mm/min):").pack(side="left")
        ttk.Entry(ff, textvariable=self._feed_rate, width=8).pack(side="left", padx=5)

    def _jog(self, dx=0, dy=0, z=0, home=False):
        step = self._step_size.get()
        feed = self._feed_rate.get()
        if home:
            self._jog_callback(0, 0, 0, feed, home=True)
        else:
            self._jog_callback(dx*step, dy*step, z*step, feed)

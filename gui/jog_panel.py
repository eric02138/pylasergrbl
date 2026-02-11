"""Jog control panel widget.

Provides manual jogging controls similar to LaserGRBL's jog panel:
arrow buttons, step size selector, feed rate, and homing/unlock buttons.
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable


class JogPanel(ttk.LabelFrame):
    """Manual jog control panel."""

    STEP_SIZES = [0.1, 0.5, 1.0, 5.0, 10.0, 50.0, 100.0]
    DEFAULT_FEED = 1000

    def __init__(self, parent, jog_callback: Callable, **kwargs):
        super().__init__(parent, text="Jog Controls", **kwargs)
        self._jog_callback = jog_callback
        self._step_size = tk.DoubleVar(value=1.0)
        self._feed_rate = tk.IntVar(value=self.DEFAULT_FEED)
        self._build_ui()

    def _build_ui(self):
        # Direction buttons in a grid
        btn_frame = ttk.Frame(self)
        btn_frame.pack(padx=5, pady=5)

        btn_style = {"width": 4}

        # Y+
        ttk.Button(btn_frame, text="Y+", command=lambda: self._jog(0, 1), **btn_style).grid(row=0, column=1, padx=2, pady=2)
        # X- Home X+
        ttk.Button(btn_frame, text="X-", command=lambda: self._jog(-1, 0), **btn_style).grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="⌂", command=lambda: self._jog(0, 0, home=True), **btn_style).grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="X+", command=lambda: self._jog(1, 0), **btn_style).grid(row=1, column=2, padx=2, pady=2)
        # Y-
        ttk.Button(btn_frame, text="Y-", command=lambda: self._jog(0, -1), **btn_style).grid(row=2, column=1, padx=2, pady=2)

        # Z buttons (optional)
        ttk.Button(btn_frame, text="Z+", command=lambda: self._jog(0, 0, z=1), **btn_style).grid(row=0, column=3, padx=8, pady=2)
        ttk.Button(btn_frame, text="Z-", command=lambda: self._jog(0, 0, z=-1), **btn_style).grid(row=2, column=3, padx=8, pady=2)

        # Step size selector
        step_frame = ttk.Frame(self)
        step_frame.pack(padx=5, pady=2, fill="x")
        ttk.Label(step_frame, text="Step (mm):").pack(side="left")
        step_combo = ttk.Combobox(step_frame, textvariable=self._step_size,
                                   values=self.STEP_SIZES, width=8)
        step_combo.pack(side="left", padx=5)

        # Feed rate
        feed_frame = ttk.Frame(self)
        feed_frame.pack(padx=5, pady=2, fill="x")
        ttk.Label(feed_frame, text="Feed (mm/min):").pack(side="left")
        ttk.Entry(feed_frame, textvariable=self._feed_rate, width=8).pack(side="left", padx=5)

    def _jog(self, dx: int, dy: int, z: int = 0, home: bool = False):
        step = self._step_size.get()
        feed = self._feed_rate.get()
        if home:
            self._jog_callback(0, 0, 0, feed, home=True)
        else:
            self._jog_callback(dx * step, dy * step, z * step, feed)

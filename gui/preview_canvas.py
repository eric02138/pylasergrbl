"""G-code toolpath preview canvas."""

import tkinter as tk
from typing import List, Tuple, Optional
from core.gcode_parser import GCodeFile


class PreviewCanvas(tk.Canvas):
    RAPID_COLOR = "#888888"
    CUT_COLOR = "#FF4444"
    PROGRESS_COLOR = "#44FF44"
    BG_COLOR = "#1E1E1E"
    GRID_COLOR = "#333333"
    ORIGIN_COLOR = "#FFFF00"

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("bg", self.BG_COLOR)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, **kwargs)
        self._file: Optional[GCodeFile] = None
        self._toolpath: List[Tuple[float, float, bool]] = []
        self._bounds = (0, 0, 100, 100)
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0
        self._progress_index = 0
        self._drag_start = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_drag_start)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<MouseWheel>", self._on_scroll)
        self.bind("<Button-4>", lambda e: self._zoom(1.1))
        self.bind("<Button-5>", lambda e: self._zoom(0.9))

    def set_file(self, gcode_file: GCodeFile):
        self._file = gcode_file
        self._toolpath = gcode_file.get_toolpath()
        self._bounds = gcode_file.get_bounds()
        self._progress_index = 0
        self._fit_to_view()
        self.redraw()

    def set_progress(self, index: int):
        self._progress_index = index
        self.redraw()

    def clear(self):
        self._file = None
        self._toolpath = []
        self.delete("all")

    def _fit_to_view(self):
        if not self._toolpath:
            return
        min_x, min_y, max_x, max_y = self._bounds
        w = self.winfo_width() or 400
        h = self.winfo_height() or 400
        rx = max_x - min_x or 1
        ry = max_y - min_y or 1
        margin = 20
        self._scale = min((w - 2*margin) / rx, (h - 2*margin) / ry)
        self._offset_x = margin + (w - 2*margin - rx * self._scale) / 2 - min_x * self._scale
        self._offset_y = margin + (h - 2*margin - ry * self._scale) / 2 - min_y * self._scale

    def _to_canvas(self, x, y):
        cx = x * self._scale + self._offset_x
        cy = (self.winfo_height() or 400) - (y * self._scale + self._offset_y)
        return cx, cy

    def redraw(self):
        self.delete("all")
        if not self._toolpath:
            w = self.winfo_width() or 400
            h = self.winfo_height() or 400
            self.create_text(w//2, h//2, text="No file loaded\nLoad G-code, image, or SVG",
                             fill="#666666", font=("sans-serif", 12), justify="center")
            return
        self._draw_grid()
        self._draw_origin()
        self._draw_toolpath()
        self._draw_bounds_info()

    def _draw_grid(self):
        w = self.winfo_width() or 400
        h = self.winfo_height() or 400
        grid_mm = 10
        if self._scale > 0:
            while grid_mm * self._scale < 30: grid_mm *= 5
            while grid_mm * self._scale > 200: grid_mm /= 5
        min_x, min_y, max_x, max_y = self._bounds
        val = min_x - grid_mm
        while val < max_x + grid_mm:
            cx, _ = self._to_canvas(val, 0)
            self.create_line(cx, 0, cx, h, fill=self.GRID_COLOR)
            val += grid_mm
        val = min_y - grid_mm
        while val < max_y + grid_mm:
            _, cy = self._to_canvas(0, val)
            self.create_line(0, cy, w, cy, fill=self.GRID_COLOR)
            val += grid_mm

    def _draw_origin(self):
        cx, cy = self._to_canvas(0, 0)
        self.create_line(cx-8, cy, cx+8, cy, fill=self.ORIGIN_COLOR, width=2)
        self.create_line(cx, cy-8, cx, cy+8, fill=self.ORIGIN_COLOR, width=2)

    def _draw_toolpath(self):
        if len(self._toolpath) < 2:
            return
        px, py = 0.0, 0.0
        for i, (x, y, cutting) in enumerate(self._toolpath):
            cx1, cy1 = self._to_canvas(px, py)
            cx2, cy2 = self._to_canvas(x, y)
            color = self.PROGRESS_COLOR if i < self._progress_index else (self.CUT_COLOR if cutting else self.RAPID_COLOR)
            self.create_line(cx1, cy1, cx2, cy2, fill=color, width=2 if cutting else 1)
            px, py = x, y

    def _draw_bounds_info(self):
        min_x, min_y, max_x, max_y = self._bounds
        self.create_text(10, 10, text=f"{max_x-min_x:.1f} x {max_y-min_y:.1f} mm",
                         fill="#AAAAAA", font=("monospace", 10), anchor="nw")

    def _on_resize(self, e):
        if self._toolpath:
            self._fit_to_view()
            self.redraw()

    def _on_drag_start(self, e):
        self._drag_start = (e.x, e.y)

    def _on_drag(self, e):
        if self._drag_start:
            self._offset_x += e.x - self._drag_start[0]
            self._offset_y -= e.y - self._drag_start[1]
            self._drag_start = (e.x, e.y)
            self.redraw()

    def _on_scroll(self, e):
        self._zoom(1.1 if e.delta > 0 else 0.9)

    def _zoom(self, factor):
        self._scale *= factor
        self.redraw()

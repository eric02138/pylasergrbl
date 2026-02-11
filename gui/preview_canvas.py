"""G-code toolpath preview canvas.

Renders a visual preview of the loaded G-code toolpath,
similar to LaserGRBL's preview panel.
"""

import tkinter as tk
from typing import List, Tuple, Optional

from core.gcode_parser import GCodeFile


class PreviewCanvas(tk.Canvas):
    """Canvas widget that renders G-code toolpath preview."""

    RAPID_COLOR = "#888888"      # Gray for rapid moves
    CUT_COLOR = "#FF4444"        # Red for cutting moves
    PROGRESS_COLOR = "#44FF44"   # Green for completed moves
    BG_COLOR = "#1E1E1E"         # Dark background
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

        # Pan/zoom support
        self._drag_start = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_drag_start)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<MouseWheel>", self._on_scroll)
        self.bind("<Button-4>", lambda e: self._zoom(1.1))
        self.bind("<Button-5>", lambda e: self._zoom(0.9))

    def set_file(self, gcode_file: GCodeFile):
        """Load a G-code file and render its preview."""
        self._file = gcode_file
        self._toolpath = gcode_file.get_toolpath()
        self._bounds = gcode_file.get_bounds()
        self._progress_index = 0
        self._fit_to_view()
        self.redraw()

    def set_progress(self, index: int):
        """Update the progress indicator."""
        self._progress_index = index
        self.redraw()

    def clear(self):
        self._file = None
        self._toolpath = []
        self.delete("all")

    def _fit_to_view(self):
        """Scale and center the toolpath to fit the canvas."""
        if not self._toolpath:
            return

        min_x, min_y, max_x, max_y = self._bounds
        width = self.winfo_width() or 400
        height = self.winfo_height() or 400

        range_x = max_x - min_x or 1
        range_y = max_y - min_y or 1

        margin = 20
        scale_x = (width - 2 * margin) / range_x
        scale_y = (height - 2 * margin) / range_y
        self._scale = min(scale_x, scale_y)

        # Center the drawing
        self._offset_x = margin + (width - 2 * margin - range_x * self._scale) / 2 - min_x * self._scale
        self._offset_y = margin + (height - 2 * margin - range_y * self._scale) / 2 - min_y * self._scale

    def _to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        """Convert work coordinates to canvas coordinates."""
        cx = x * self._scale + self._offset_x
        cy = (self.winfo_height() or 400) - (y * self._scale + self._offset_y)  # Flip Y
        return cx, cy

    def redraw(self):
        """Redraw the entire preview."""
        self.delete("all")
        if not self._toolpath:
            self._draw_empty_message()
            return

        self._draw_grid()
        self._draw_origin()
        self._draw_toolpath()
        self._draw_bounds_info()

    def _draw_empty_message(self):
        w = self.winfo_width() or 400
        h = self.winfo_height() or 400
        self.create_text(w // 2, h // 2, text="No file loaded\nLoad a G-code file or import an image",
                         fill="#666666", font=("sans-serif", 12), justify="center")

    def _draw_grid(self):
        """Draw a simple grid."""
        w = self.winfo_width() or 400
        h = self.winfo_height() or 400

        # Determine grid spacing (10mm, 50mm, etc.)
        grid_mm = 10
        if self._scale > 0:
            while grid_mm * self._scale < 30:
                grid_mm *= 5
            while grid_mm * self._scale > 200:
                grid_mm /= 5

        min_x, min_y, max_x, max_y = self._bounds
        for gx in _frange(min_x - grid_mm, max_x + grid_mm, grid_mm):
            cx, _ = self._to_canvas(gx, 0)
            self.create_line(cx, 0, cx, h, fill=self.GRID_COLOR, width=1)
        for gy in _frange(min_y - grid_mm, max_y + grid_mm, grid_mm):
            _, cy = self._to_canvas(0, gy)
            self.create_line(0, cy, w, cy, fill=self.GRID_COLOR, width=1)

    def _draw_origin(self):
        """Draw origin crosshair."""
        cx, cy = self._to_canvas(0, 0)
        size = 8
        self.create_line(cx - size, cy, cx + size, cy, fill=self.ORIGIN_COLOR, width=2)
        self.create_line(cx, cy - size, cx, cy + size, fill=self.ORIGIN_COLOR, width=2)

    def _draw_toolpath(self):
        """Draw the toolpath lines."""
        if len(self._toolpath) < 2:
            return

        prev_x, prev_y = 0.0, 0.0
        for i, (x, y, cutting) in enumerate(self._toolpath):
            cx1, cy1 = self._to_canvas(prev_x, prev_y)
            cx2, cy2 = self._to_canvas(x, y)

            if i < self._progress_index:
                color = self.PROGRESS_COLOR
            elif cutting:
                color = self.CUT_COLOR
            else:
                color = self.RAPID_COLOR

            width = 2 if cutting else 1
            self.create_line(cx1, cy1, cx2, cy2, fill=color, width=width)
            prev_x, prev_y = x, y

    def _draw_bounds_info(self):
        """Draw bounding box info text."""
        min_x, min_y, max_x, max_y = self._bounds
        w_mm = max_x - min_x
        h_mm = max_y - min_y
        info = f"{w_mm:.1f} × {h_mm:.1f} mm"
        self.create_text(10, 10, text=info, fill="#AAAAAA",
                         font=("monospace", 10), anchor="nw")

    # Pan/zoom handlers
    def _on_resize(self, event):
        if self._toolpath:
            self._fit_to_view()
            self.redraw()

    def _on_drag_start(self, event):
        self._drag_start = (event.x, event.y)

    def _on_drag(self, event):
        if self._drag_start:
            dx = event.x - self._drag_start[0]
            dy = event.y - self._drag_start[1]
            self._offset_x += dx
            self._offset_y -= dy  # Inverted Y
            self._drag_start = (event.x, event.y)
            self.redraw()

    def _on_scroll(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        self._zoom(factor)

    def _zoom(self, factor: float):
        self._scale *= factor
        self.redraw()


def _frange(start, stop, step):
    """Float range generator."""
    val = start
    while val < stop:
        yield val
        val += step

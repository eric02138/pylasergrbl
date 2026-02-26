"""Main application window with SVG import support."""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import logging

from core.grbl_controller import GrblController, MachineStatus, THREADING_MODES
from core.gcode_parser import GCodeFile
from gui.preview_canvas import PreviewCanvas
from gui.jog_panel import JogPanel
from converters.image_to_gcode import (
    image_to_gcode, ScanDirection, ConversionMode, DitherMethod
)
from converters.svg_to_gcode import svg_to_gcode, SvgConvertSettings, get_svg_info
from utils.serial_utils import list_serial_ports, COMMON_BAUD_RATES, DEFAULT_BAUD_RATE

logger = logging.getLogger(__name__)


class MainWindow:
    STATUS_COLORS = {
        MachineStatus.DISCONNECTED: "#888888",
        MachineStatus.CONNECTING: "#FFAA00",
        MachineStatus.IDLE: "#44CC44",
        MachineStatus.RUN: "#4488FF",
        MachineStatus.JOG: "#4488FF",
        MachineStatus.HOLD: "#FFAA00",
        MachineStatus.DOOR: "#FFAA00",
        MachineStatus.HOME: "#4488FF",
        MachineStatus.ALARM: "#FF4444",
        MachineStatus.CHECK: "#FFAA00",
        MachineStatus.UNKNOWN: "#888888",
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PyLaserGRBL — Laser Engraver Control")
        self.root.geometry("1400x750")
        self.root.minsize(1100, 500)
        style = ttk.Style()
        style.theme_use("clam")

        self.grbl = GrblController()
        self._setup_callbacks()

        self._port_var = tk.StringVar()
        self._baud_var = tk.IntVar(value=DEFAULT_BAUD_RATE)
        self._status_text = tk.StringVar(value="Disconnected")
        self._position_text = tk.StringVar(value="X: 0.000  Y: 0.000  Z: 0.000")
        self._progress_var = tk.DoubleVar(value=0)
        self._progress_text = tk.StringVar(value="0 / 0")
        self._threading_mode = tk.StringVar(value="Slow")
        self._loaded_file = None

        self._build_ui()
        self._refresh_ports()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_callbacks(self):
        self.grbl.on_status_change = self._on_status_change
        self.grbl.on_position_update = self._on_position_update
        self.grbl.on_progress_update = self._on_progress_update
        self.grbl.on_line_received = self._on_line_received
        self.grbl.on_error = self._on_error
        self.grbl.on_connected = self._on_connected
        self.grbl.on_disconnected = self._on_disconnected
        self.grbl.on_job_finished = self._on_job_finished

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        self._build_toolbar()
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=5, pady=5)

        left = ttk.Frame(paned)
        paned.add(left, weight=3)
        self.preview = PreviewCanvas(left, width=500, height=400)
        self.preview.pack(fill="both", expand=True)

        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        self._build_right_panel(right)
        self._build_status_bar()

    def _build_toolbar(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=5, pady=(5, 0))

        # Connection
        conn = ttk.LabelFrame(toolbar, text="Connection")
        conn.pack(side="left", padx=(0, 10))
        ttk.Label(conn, text="Port:").pack(side="left", padx=(5, 2))
        self._port_combo = ttk.Combobox(conn, textvariable=self._port_var, width=15)
        self._port_combo.pack(side="left", padx=2)
        ttk.Button(conn, text="Refresh", width=7, command=self._refresh_ports).pack(side="left")
        ttk.Label(conn, text="Baud:").pack(side="left", padx=(10, 2))
        ttk.Combobox(conn, textvariable=self._baud_var, values=COMMON_BAUD_RATES, width=8).pack(side="left", padx=2)
        self._connect_btn = ttk.Button(conn, text="Connect", command=self._toggle_connection)
        self._connect_btn.pack(side="left", padx=5, pady=3)

        # File — now includes SVG import
        ffile = ttk.LabelFrame(toolbar, text="File")
        ffile.pack(side="left", padx=(0, 10))
        ttk.Button(ffile, text="Open G-code", command=self._open_gcode).pack(side="left", padx=3, pady=3)
        ttk.Button(ffile, text="Import Image", command=self._import_image).pack(side="left", padx=3, pady=3)
        ttk.Button(ffile, text="Import SVG", command=self._import_svg).pack(side="left", padx=3, pady=3)

        # Job
        job = ttk.LabelFrame(toolbar, text="Job")
        job.pack(side="left", padx=(0, 10))
        self._start_btn = ttk.Button(job, text="Start", command=self._start_job)
        self._start_btn.pack(side="left", padx=3, pady=3)
        self._pause_btn = ttk.Button(job, text="Pause", command=self._pause_job)
        self._pause_btn.pack(side="left", padx=3, pady=3)
        self._abort_btn = ttk.Button(job, text="Abort", command=self._abort_job)
        self._abort_btn.pack(side="left", padx=3, pady=3)

        # Speed
        spd = ttk.LabelFrame(toolbar, text="Speed")
        spd.pack(side="left", padx=(0, 10))
        ttk.Combobox(spd, textvariable=self._threading_mode,
                      values=list(THREADING_MODES.keys()), width=10,
                      state="readonly").pack(padx=3, pady=3)
        self._threading_mode.trace_add("write",
            lambda *_: self.grbl.set_threading_mode(self._threading_mode.get()))

    def _build_right_panel(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        ctrl = ttk.Frame(nb)
        nb.add(ctrl, text="Controls")

        JogPanel(ctrl, jog_callback=self._do_jog).pack(fill="x", padx=5, pady=5)

        cmds = ttk.LabelFrame(ctrl, text="Quick Commands")
        cmds.pack(fill="x", padx=5, pady=5)
        for i, (t, c) in enumerate([
            ("Home ($H)", lambda: self.grbl.homing()),
            ("Unlock ($X)", lambda: self.grbl.kill_alarm()),
            ("Soft Reset", lambda: self.grbl.soft_reset()),
            ("Set Zero", lambda: self.grbl.set_zero()),
            ("Laser Test", lambda: self._laser_test()),
            ("Settings ($$)", lambda: self.grbl.request_settings()),
        ]):
            ttk.Button(cmds, text=t, command=c).grid(row=i//2, column=i%2, padx=3, pady=2, sticky="ew")
        cmds.columnconfigure(0, weight=1)
        cmds.columnconfigure(1, weight=1)

        mf = ttk.LabelFrame(ctrl, text="Manual Command")
        mf.pack(fill="x", padx=5, pady=5)
        self._manual_entry = ttk.Entry(mf)
        self._manual_entry.pack(side="left", fill="x", expand=True, padx=3, pady=3)
        self._manual_entry.bind("<Return>", self._send_manual)
        ttk.Button(mf, text="Send", command=self._send_manual).pack(side="right", padx=3, pady=3)

        con_tab = ttk.Frame(nb)
        nb.add(con_tab, text="Console")
        self._console = scrolledtext.ScrolledText(con_tab, height=20, font=("Courier", 9),
            bg="#1E1E1E", fg="#CCCCCC", insertbackground="#FFFFFF")
        self._console.pack(fill="both", expand=True, padx=3, pady=3)
        self._console.tag_config("tx", foreground="#88CCFF")
        self._console.tag_config("rx", foreground="#CCCCCC")
        self._console.tag_config("error", foreground="#FF6666")
        self._console.tag_config("info", foreground="#66CC66")

    def _build_status_bar(self):
        sb = ttk.Frame(self.root)
        sb.pack(fill="x", padx=5, pady=(0, 5))
        self._status_label = tk.Label(sb, textvariable=self._status_text, fg="#44CC44",
            bg="#2A2A2A", font=("sans-serif", 10, "bold"), padx=10, pady=2)
        self._status_label.pack(side="left")
        tk.Label(sb, textvariable=self._position_text, font=("Courier", 10),
                 bg="#2A2A2A", fg="#AAAAAA", padx=10, pady=2).pack(side="left", padx=10)
        self._progress_bar = ttk.Progressbar(sb, variable=self._progress_var, maximum=100, length=200)
        self._progress_bar.pack(side="right", padx=5)
        tk.Label(sb, textvariable=self._progress_text, font=("sans-serif", 9), padx=5).pack(side="right")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _refresh_ports(self):
        ports = list_serial_ports()
        self._port_combo["values"] = ports
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    def _toggle_connection(self):
        if self.grbl.is_connected:
            self.grbl.disconnect()
        else:
            port = self._port_var.get()
            baud = self._baud_var.get()
            if not port:
                messagebox.showwarning("No Port", "Please select a serial port.")
                return
            self._log("info", f"Connecting to {port} @ {baud}...")
            threading.Thread(target=self.grbl.connect, args=(port, baud), daemon=True).start()

    # ------------------------------------------------------------------
    # Open G-code
    # ------------------------------------------------------------------
    def _open_gcode(self):
        path = filedialog.askopenfilename(
            title="Open G-code File",
            filetypes=[("G-code files", "*.gcode *.nc *.gc *.ngc *.txt"), ("All files", "*.*")]
        )
        if path:
            try:
                gf = GCodeFile.from_file(path)
                self._set_loaded_file(gf, os.path.basename(path))
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load file:\n{e}")

    # ------------------------------------------------------------------
    # Import raster image
    # ------------------------------------------------------------------
    def _import_image(self):
        path = filedialog.askopenfilename(
            title="Import Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff"), ("All files", "*.*")]
        )
        if path:
            self._show_image_dialog(path)

    def _show_image_dialog(self, image_path):
        dlg = tk.Toplevel(self.root)
        dlg.title("Image to G-code Settings")
        dlg.geometry("420x560")
        dlg.transient(self.root)
        dlg.grab_set()

        fr = ttk.Frame(dlg, padding=10)
        fr.pack(fill="both", expand=True)
        ttk.Label(fr, text=f"File: {os.path.basename(image_path)}",
                  font=("sans-serif", 10, "bold")).pack(anchor="w", pady=(0, 5))

        entries = {}
        def add(label, key, default):
            row = ttk.Frame(fr); row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=25, anchor="w").pack(side="left")
            v = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=v, width=12).pack(side="left")
            entries[key] = v

        add("Resolution (DPI)", "resolution", 254)
        add("Width (mm, 0=auto)", "width", 0)
        add("Height (mm, 0=auto)", "height", 0)
        add("Feed Rate (mm/min)", "feed", 1000)
        add("Max Power (S)", "max_power", 1000)
        add("Min Power (S)", "min_power", 0)

        scan_var = tk.StringVar(value="HORIZONTAL")
        row = ttk.Frame(fr); row.pack(fill="x", pady=2)
        ttk.Label(row, text="Scan Direction", width=25, anchor="w").pack(side="left")
        ttk.Combobox(row, textvariable=scan_var, values=["HORIZONTAL","VERTICAL","DIAGONAL"],
                      width=14, state="readonly").pack(side="left")

        mode_var = tk.StringVar(value="DITHERING")
        row = ttk.Frame(fr); row.pack(fill="x", pady=2)
        ttk.Label(row, text="Conversion Mode", width=25, anchor="w").pack(side="left")
        ttk.Combobox(row, textvariable=mode_var, values=["DITHERING", "LINE_TO_LINE"],
                      width=14, state="readonly").pack(side="left")

        dither_var = tk.StringVar(value="FLOYD_STEINBERG")
        row = ttk.Frame(fr); row.pack(fill="x", pady=2)
        ttk.Label(row, text="Dither Method", width=25, anchor="w").pack(side="left")
        ttk.Combobox(row, textvariable=dither_var,
                      values=["FLOYD_STEINBERG","ORDERED_4X4","ATKINSON","THRESHOLD"],
                      width=16, state="readonly").pack(side="left")

        invert_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="Invert image", variable=invert_var).pack(anchor="w", pady=2)
        laser_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="Dynamic laser mode (M4)", variable=laser_var).pack(anchor="w", pady=2)

        def do_convert():
            try:
                w = float(entries["width"].get())
                h = float(entries["height"].get())
                dlg.destroy()
                self._log("info", "Converting image to G-code...")
                def run():
                    lines = image_to_gcode(
                        image_path=image_path,
                        resolution_dpi=float(entries["resolution"].get()),
                        feed_rate=float(entries["feed"].get()),
                        max_power=int(entries["max_power"].get()),
                        min_power=int(entries["min_power"].get()),
                        scan_direction=ScanDirection[scan_var.get()],
                        conversion_mode=ConversionMode[mode_var.get()],
                        dither_method=DitherMethod[dither_var.get()],
                        invert=invert_var.get(),
                        laser_mode=laser_var.get(),
                        width_mm=w if w > 0 else None,
                        height_mm=h if h > 0 else None,
                    )
                    gf = GCodeFile.from_lines(lines, filename=image_path)
                    self.root.after(0, lambda: self._set_loaded_file(gf, os.path.basename(image_path)))
                threading.Thread(target=run, daemon=True).start()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        bf = ttk.Frame(fr); bf.pack(fill="x", pady=10)
        ttk.Button(bf, text="Convert & Load", command=do_convert).pack(side="right", padx=5)
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right")

    # ------------------------------------------------------------------
    # Import SVG — NEW
    # ------------------------------------------------------------------
    def _import_svg(self):
        """Open an SVG file and show the SVG conversion settings dialog."""
        path = filedialog.askopenfilename(
            title="Import SVG",
            filetypes=[("SVG files", "*.svg"), ("All files", "*.*")]
        )
        if path:
            self._show_svg_dialog(path)

    def _show_svg_dialog(self, svg_path: str):
        """Show SVG conversion settings dialog with live SVG info."""
        # Get SVG info for display
        info = get_svg_info(svg_path)
        dlg = tk.Toplevel(self.root)
        dlg.title("SVG to G-code Settings")
        dlg.geometry("450x620")
        dlg.transient(self.root)
        dlg.grab_set()

        fr = ttk.Frame(dlg, padding=10)
        fr.pack(fill="both", expand=True)

        # --- File info header ---
        ttk.Label(fr, text=f"File: {os.path.basename(svg_path)}",
                  font=("sans-serif", 10, "bold")).pack(anchor="w")

        info_text = ""
        if "error" in info:
            info_text = f"Parse warning: {info['error']}"
        else:
            w = info.get('width', 0)
            h = info.get('height', 0)
            pc = info.get('path_count', 0)
            vb = info.get('viewbox', '')
            info_text = (f"SVG size: {w:.1f} x {h:.1f} units  |  "
                         f"{pc} path{'s' if pc != 1 else ''}  |  viewBox: {vb or 'none'}")
        ttk.Label(fr, text=info_text, foreground="#666666",
                  font=("sans-serif", 9)).pack(anchor="w", pady=(0, 8))

        ttk.Separator(fr, orient="horizontal").pack(fill="x", pady=4)

        # --- Settings fields ---
        entries = {}
        def add(label, key, default, tooltip=""):
            row = ttk.Frame(fr); row.pack(fill="x", pady=2)
            lbl = ttk.Label(row, text=label, width=28, anchor="w")
            lbl.pack(side="left")
            v = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=v, width=12).pack(side="left")
            entries[key] = v
            if tooltip:
                ttk.Label(row, text=tooltip, foreground="#999999",
                          font=("sans-serif", 8)).pack(side="left", padx=5)

        # Pre-calculate sensible default width
        default_width = 50.0
        if info.get("width", 0) > 0 and info.get("width", 0) < 500:
            default_width = info["width"]

        ttk.Label(fr, text="Dimensions", font=("sans-serif", 9, "bold")).pack(anchor="w", pady=(4, 0))
        add("Target Width (mm)", "width", f"{default_width:.1f}", "0 = native")
        add("Target Height (mm)", "height", "0", "0 = proportional")
        add("X Offset (mm)", "offset_x", "0")
        add("Y Offset (mm)", "offset_y", "0")

        ttk.Label(fr, text="Laser Settings", font=("sans-serif", 9, "bold")).pack(anchor="w", pady=(8, 0))
        add("Cut Feed Rate (mm/min)", "feed", "500")
        add("Travel Speed (mm/min)", "travel", "3000")
        add("Laser Power (S)", "power", "1000")
        add("Number of Passes", "passes", "1", "for cutting")

        ttk.Label(fr, text="Conversion", font=("sans-serif", 9, "bold")).pack(anchor="w", pady=(8, 0))
        add("Bezier Resolution", "bezier_res", "20", "segments/curve")

        # Checkboxes
        flip_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="Flip Y axis (recommended for CNC)", variable=flip_var).pack(anchor="w", pady=2)

        optimize_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="Optimize path order (reduce travel)", variable=optimize_var).pack(anchor="w", pady=2)

        laser_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="Dynamic laser mode (M4)", variable=laser_var).pack(anchor="w", pady=2)

        # --- Convert button ---
        def do_convert():
            try:
                w = float(entries["width"].get())
                h = float(entries["height"].get())

                settings = SvgConvertSettings(
                    feed_rate=float(entries["feed"].get()),
                    travel_speed=float(entries["travel"].get()),
                    power=int(entries["power"].get()),
                    num_passes=int(entries["passes"].get()),
                    target_width_mm=w,
                    target_height_mm=h,
                    offset_x=float(entries["offset_x"].get()),
                    offset_y=float(entries["offset_y"].get()),
                    bezier_resolution=int(entries["bezier_res"].get()),
                    laser_mode=laser_var.get(),
                    flip_y=flip_var.get(),
                    optimize_paths=optimize_var.get(),
                )

                dlg.destroy()
                self._log("info", f"Converting SVG to G-code...")

                def run():
                    try:
                        lines = svg_to_gcode(svg_path, settings=settings)
                        gf = GCodeFile.from_lines(lines, filename=svg_path)
                        self.root.after(0, lambda: self._set_loaded_file(
                            gf, os.path.basename(svg_path)))
                    except Exception as e:
                        self.root.after(0, lambda: messagebox.showerror(
                            "SVG Conversion Error", str(e)))
                        self.root.after(0, lambda: self._log("error", f"SVG error: {e}"))

                threading.Thread(target=run, daemon=True).start()

            except ValueError as e:
                messagebox.showerror("Invalid Input", f"Check your settings:\n{e}")

        def do_preview():
            """Quick preview without loading — just convert and display."""
            try:
                w = float(entries["width"].get())
                h = float(entries["height"].get())
                settings = SvgConvertSettings(
                    target_width_mm=w, target_height_mm=h,
                    offset_x=float(entries["offset_x"].get()),
                    offset_y=float(entries["offset_y"].get()),
                    bezier_resolution=int(entries["bezier_res"].get()),
                    flip_y=flip_var.get(),
                    optimize_paths=optimize_var.get(),
                )
                logger.info(settings)
                lines = svg_to_gcode(svg_path, settings=settings)
                logger.info(lines)
                gf = GCodeFile.from_lines(lines, filename=svg_path)
                self.preview.set_file(gf)
                self._log("info", f"SVG preview: {gf.total} commands")
            except Exception as e:
                messagebox.showerror("Preview Error", str(e))

        bf = ttk.Frame(fr)
        bf.pack(fill="x", pady=(12, 0))
        ttk.Button(bf, text="Convert & Load", command=do_convert).pack(side="right", padx=5)
        ttk.Button(bf, text="Preview", command=do_preview).pack(side="right", padx=2)
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right")

    # ------------------------------------------------------------------
    # Common file loading
    # ------------------------------------------------------------------
    def _set_loaded_file(self, gf: GCodeFile, display_name: str):
        """Common method to set a loaded G-code file from any source."""
        self._loaded_file = gf
        self.grbl.load_file(gf)
        self.preview.set_file(gf)
        self._progress_var.set(0)
        self._progress_text.set(f"0 / {gf.total}")
        self._log("info", f"Loaded: {display_name} ({gf.total} commands)")

    # ------------------------------------------------------------------
    # Job control
    # ------------------------------------------------------------------
    def _start_job(self):
        if not self.grbl.is_connected:
            messagebox.showwarning("Not Connected", "Connect to engraver first.")
            return
        if not self._loaded_file:
            messagebox.showwarning("No File", "Load a G-code file, image, or SVG first.")
            return
        self._log("info", "Starting job...")
        self.grbl.start_stream()

    def _pause_job(self):
        if self.grbl.is_streaming:
            self.grbl.pause_stream()
            self._log("info", "Job paused")
        elif self.grbl._paused:
            self.grbl.resume_stream()
            self._log("info", "Job resumed")

    def _abort_job(self):
        if self.grbl._streaming:
            if messagebox.askyesno("Abort Job", "Abort the current job?"):
                self.grbl.abort_stream()
                self._log("info", "Job aborted")

    def _do_jog(self, x, y, z, feed, home=False):
        if not self.grbl.is_connected:
            return
        if home:
            self.grbl.send_command("G0 X0 Y0 Z0")
        else:
            self.grbl.jog(x=x, y=y, z=z, feed=feed)

    def _laser_test(self):
        if self.grbl.is_connected:
            self.grbl.send_command("M3 S10")
            self.root.after(500, lambda: self.grbl.send_command("M5 S0"))

    def _send_manual(self, event=None):
        cmd = self._manual_entry.get().strip()
        if cmd and self.grbl.is_connected:
            self._log("tx", f">>> {cmd}")
            self.grbl.send_command(cmd)
            self._manual_entry.delete(0, "end")

    # ------------------------------------------------------------------
    # Console
    # ------------------------------------------------------------------
    def _log(self, tag, text):
        def _do():
            self._console.insert("end", text + "\n", tag)
            self._console.see("end")
        self.root.after(0, _do)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _on_status_change(self, status):
        def _do():
            self._status_text.set(status.name)
            self._status_label.config(fg=self.STATUS_COLORS.get(status, "#888888"))
        self.root.after(0, _do)

    def _on_position_update(self):
        def _do():
            self._position_text.set(
                f"X: {self.grbl.work_x:8.3f}  Y: {self.grbl.work_y:8.3f}  Z: {self.grbl.work_z:8.3f}")
        self.root.after(0, _do)

    def _on_progress_update(self, pct):
        def _do():
            self._progress_var.set(pct)
            if self._loaded_file:
                self._progress_text.set(f"{self._loaded_file.ok_count} / {self._loaded_file.total}")
                self.preview.set_progress(self._loaded_file.ok_count)
        self.root.after(0, _do)

    def _on_line_received(self, line):
        if not line.startswith("<"):
            self._log("rx", line)

    def _on_error(self, msg):
        self._log("error", msg)

    def _on_connected(self):
        def _do():
            self._connect_btn.config(text="Disconnect")
            self._log("info", f"Connected — GRBL {self.grbl.grbl_version or 'unknown'}")
        self.root.after(0, _do)

    def _on_disconnected(self):
        def _do():
            self._connect_btn.config(text="Connect")
            self._log("info", "Disconnected")
        self.root.after(0, _do)

    def _on_job_finished(self):
        def _do():
            if self._loaded_file:
                self._log("info",
                    f"Job complete: {self._loaded_file.ok_count}/{self._loaded_file.total} OK, "
                    f"{self._loaded_file.error_count} errors")
            self._progress_var.set(100)
        self.root.after(0, _do)

    def _on_close(self):
        if self.grbl.is_connected:
            self.grbl.disconnect()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

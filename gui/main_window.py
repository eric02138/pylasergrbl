"""Main application window.

Tkinter-based GUI that mirrors LaserGRBL's main form:
- Connection panel (port, baud, connect/disconnect)
- Job controls (start, pause, abort)
- Preview canvas
- Jog controls
- Status bar with position and progress
- Console log
- Image import dialog
"""

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
from utils.serial_utils import list_serial_ports, COMMON_BAUD_RATES, DEFAULT_BAUD_RATE

logger = logging.getLogger(__name__)


class MainWindow:
    """Main application window."""

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
        self.root.geometry("1100x750")
        self.root.minsize(800, 500)

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
        self._threading_mode = tk.StringVar(value="Fast")
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
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        self._build_toolbar()

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=5, pady=5)

        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=3)
        self.preview = PreviewCanvas(left_frame, width=500, height=400)
        self.preview.pack(fill="both", expand=True)

        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)
        self._build_right_panel(right_frame)

        self._build_status_bar()

    def _build_toolbar(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=5, pady=(5, 0))

        # Connection
        conn_frame = ttk.LabelFrame(toolbar, text="Connection")
        conn_frame.pack(side="left", padx=(0, 10))

        ttk.Label(conn_frame, text="Port:").pack(side="left", padx=(5, 2))
        self._port_combo = ttk.Combobox(conn_frame, textvariable=self._port_var, width=15)
        self._port_combo.pack(side="left", padx=2)
        ttk.Button(conn_frame, text="Refresh", width=7, command=self._refresh_ports).pack(side="left")

        ttk.Label(conn_frame, text="Baud:").pack(side="left", padx=(10, 2))
        ttk.Combobox(conn_frame, textvariable=self._baud_var,
                      values=COMMON_BAUD_RATES, width=8).pack(side="left", padx=2)

        self._connect_btn = ttk.Button(conn_frame, text="Connect", command=self._toggle_connection)
        self._connect_btn.pack(side="left", padx=5, pady=3)

        # File
        file_frame = ttk.LabelFrame(toolbar, text="File")
        file_frame.pack(side="left", padx=(0, 10))
        ttk.Button(file_frame, text="Open G-code", command=self._open_gcode).pack(side="left", padx=3, pady=3)
        ttk.Button(file_frame, text="Import Image", command=self._import_image).pack(side="left", padx=3, pady=3)

        # Job
        job_frame = ttk.LabelFrame(toolbar, text="Job")
        job_frame.pack(side="left", padx=(0, 10))
        self._start_btn = ttk.Button(job_frame, text="Start", command=self._start_job)
        self._start_btn.pack(side="left", padx=3, pady=3)
        self._pause_btn = ttk.Button(job_frame, text="Pause", command=self._pause_job)
        self._pause_btn.pack(side="left", padx=3, pady=3)
        self._abort_btn = ttk.Button(job_frame, text="Abort", command=self._abort_job)
        self._abort_btn.pack(side="left", padx=3, pady=3)

        # Threading mode
        settings_frame = ttk.LabelFrame(toolbar, text="Speed")
        settings_frame.pack(side="left", padx=(0, 10))
        ttk.Combobox(settings_frame, textvariable=self._threading_mode,
                      values=list(THREADING_MODES.keys()), width=10,
                      state="readonly").pack(padx=3, pady=3)
        self._threading_mode.trace_add("write",
            lambda *_: self.grbl.set_threading_mode(self._threading_mode.get()))

    def _build_right_panel(self, parent):
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)

        # Tab: Controls
        ctrl_tab = ttk.Frame(notebook)
        notebook.add(ctrl_tab, text="Controls")

        jog = JogPanel(ctrl_tab, jog_callback=self._do_jog)
        jog.pack(fill="x", padx=5, pady=5)

        cmd_frame = ttk.LabelFrame(ctrl_tab, text="Quick Commands")
        cmd_frame.pack(fill="x", padx=5, pady=5)

        buttons = [
            ("Home ($H)",    lambda: self.grbl.homing()),
            ("Unlock ($X)",  lambda: self.grbl.kill_alarm()),
            ("Soft Reset",   lambda: self.grbl.soft_reset()),
            ("Set Zero",     lambda: self.grbl.set_zero()),
            ("Laser Test",   lambda: self._laser_test()),
            ("Settings ($$)", lambda: self.grbl.request_settings()),
        ]
        for i, (text, cmd) in enumerate(buttons):
            ttk.Button(cmd_frame, text=text, command=cmd).grid(
                row=i // 2, column=i % 2, padx=3, pady=2, sticky="ew")
        cmd_frame.columnconfigure(0, weight=1)
        cmd_frame.columnconfigure(1, weight=1)

        # Manual command
        manual_frame = ttk.LabelFrame(ctrl_tab, text="Manual Command")
        manual_frame.pack(fill="x", padx=5, pady=5)
        self._manual_entry = ttk.Entry(manual_frame)
        self._manual_entry.pack(side="left", fill="x", expand=True, padx=3, pady=3)
        self._manual_entry.bind("<Return>", self._send_manual)
        ttk.Button(manual_frame, text="Send", command=self._send_manual).pack(side="right", padx=3, pady=3)

        # Tab: Console
        console_tab = ttk.Frame(notebook)
        notebook.add(console_tab, text="Console")

        self._console = scrolledtext.ScrolledText(
            console_tab, height=20, font=("Courier", 9),
            bg="#1E1E1E", fg="#CCCCCC", insertbackground="#FFFFFF"
        )
        self._console.pack(fill="both", expand=True, padx=3, pady=3)
        self._console.tag_config("tx", foreground="#88CCFF")
        self._console.tag_config("rx", foreground="#CCCCCC")
        self._console.tag_config("error", foreground="#FF6666")
        self._console.tag_config("info", foreground="#66CC66")

    def _build_status_bar(self):
        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill="x", padx=5, pady=(0, 5))

        self._status_label = tk.Label(
            status_bar, textvariable=self._status_text,
            fg="#44CC44", bg="#2A2A2A", font=("sans-serif", 10, "bold"),
            padx=10, pady=2
        )
        self._status_label.pack(side="left")

        tk.Label(status_bar, textvariable=self._position_text,
                 font=("Courier", 10), bg="#2A2A2A", fg="#AAAAAA",
                 padx=10, pady=2).pack(side="left", padx=10)

        self._progress_bar = ttk.Progressbar(
            status_bar, variable=self._progress_var, maximum=100, length=200
        )
        self._progress_bar.pack(side="right", padx=5)
        tk.Label(status_bar, textvariable=self._progress_text,
                 font=("sans-serif", 9), padx=5).pack(side="right")

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
    # File operations
    # ------------------------------------------------------------------
    def _open_gcode(self):
        path = filedialog.askopenfilename(
            title="Open G-code File",
            filetypes=[("G-code files", "*.gcode *.nc *.gc *.ngc *.txt"), ("All files", "*.*")]
        )
        if path:
            try:
                gcode_file = GCodeFile.from_file(path)
                self._loaded_file = gcode_file
                self.grbl.load_file(gcode_file)
                self.preview.set_file(gcode_file)
                self._progress_text.set(f"0 / {gcode_file.total}")
                self._log("info", f"Loaded: {os.path.basename(path)} ({gcode_file.total} commands)")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load file:\n{e}")

    def _import_image(self):
        path = filedialog.askopenfilename(
            title="Import Image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff"),
                ("All files", "*.*"),
            ]
        )
        if not path:
            return
        self._show_image_dialog(path)

    def _show_image_dialog(self, image_path: str):
        """Show image conversion settings dialog."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Image to G-code Settings")
        dlg.geometry("420x580")
        dlg.transient(self.root)
        dlg.grab_set()

        frame = ttk.Frame(dlg, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"File: {os.path.basename(image_path)}",
                  font=("sans-serif", 10, "bold")).pack(anchor="w", pady=(0, 5))

        entries = {}

        def add_field(label_text, key, default):
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label_text, width=25, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=var, width=12).pack(side="left")
            entries[key] = var

        add_field("Resolution (DPI)", "resolution", 254)
        add_field("Width (mm, 0=auto)", "width", 50)
        add_field("Height (mm, 0=auto)", "height", 0)
        add_field("Feed Rate (mm/min)", "feed", 1000)
        add_field("Max Power (S)", "max_power", 1000)
        add_field("Min Power (S)", "min_power", 0)
        add_field("Border Speed (mm/min)", "border_speed", 3000)

        # Scan direction
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Scan Direction", width=25, anchor="w").pack(side="left")
        scan_var = tk.StringVar(value="HORIZONTAL")
        ttk.Combobox(row, textvariable=scan_var,
                      values=["HORIZONTAL", "VERTICAL", "DIAGONAL"],
                      width=14, state="readonly").pack(side="left")

        # Conversion mode
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Conversion Mode", width=25, anchor="w").pack(side="left")
        mode_var = tk.StringVar(value="LINE_TO_LINE")
        ttk.Combobox(row, textvariable=mode_var,
                      values=["LINE_TO_LINE", "DITHERING"],
                      width=14, state="readonly").pack(side="left")

        # Dither method
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Dither Method", width=25, anchor="w").pack(side="left")
        dither_var = tk.StringVar(value="FLOYD_STEINBERG")
        ttk.Combobox(row, textvariable=dither_var,
                      values=["FLOYD_STEINBERG", "ORDERED_4X4", "ATKINSON", "THRESHOLD"],
                      width=16, state="readonly").pack(side="left")

        invert_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Invert image", variable=invert_var).pack(anchor="w", pady=2)

        laser_mode_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Dynamic laser mode (M4)", variable=laser_mode_var).pack(anchor="w", pady=2)

        sharpen_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Sharpen", variable=sharpen_var).pack(anchor="w", pady=2)

        def do_convert():
            try:
                res = float(entries["resolution"].get())
                width = float(entries["width"].get())
                height = float(entries["height"].get())
                feed = float(entries["feed"].get())
                max_p = int(entries["max_power"].get())
                min_p = int(entries["min_power"].get())
                bspeed = float(entries["border_speed"].get())

                scan_dir = ScanDirection[scan_var.get()]
                conv_mode = ConversionMode[mode_var.get()]
                dith = DitherMethod[dither_var.get()]

                self._log("info", "Converting image to G-code...")
                dlg.destroy()

                def convert_thread():
                    lines = image_to_gcode(
                        image_path=image_path,
                        resolution_dpi=res,
                        feed_rate=feed,
                        max_power=max_p,
                        min_power=min_p,
                        scan_direction=scan_dir,
                        conversion_mode=conv_mode,
                        dither_method=dith,
                        invert=invert_var.get(),
                        border_speed=bspeed,
                        laser_mode=laser_mode_var.get(),
                        width_mm=width if width > 0 else None,
                        height_mm=height if height > 0 else None,
                        sharpen=sharpen_var.get(),
                    )
                    gcode_file = GCodeFile.from_lines(lines, filename=image_path)
                    self.root.after(0, lambda: self._load_generated_gcode(gcode_file))

                threading.Thread(target=convert_thread, daemon=True).start()
            except Exception as e:
                messagebox.showerror("Conversion Error", str(e))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=10)
        ttk.Button(btn_frame, text="Convert & Load", command=do_convert).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(side="right")

    def _load_generated_gcode(self, gcode_file: GCodeFile):
        self._loaded_file = gcode_file
        self.grbl.load_file(gcode_file)
        self.preview.set_file(gcode_file)
        self._progress_text.set(f"0 / {gcode_file.total}")
        self._log("info", f"Image converted: {gcode_file.total} G-code commands generated")

    # ------------------------------------------------------------------
    # Job control
    # ------------------------------------------------------------------
    def _start_job(self):
        if not self.grbl.is_connected:
            messagebox.showwarning("Not Connected", "Connect to engraver first.")
            return
        if not self._loaded_file:
            messagebox.showwarning("No File", "Load a G-code file or import an image first.")
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
            if messagebox.askyesno("Abort Job", "Are you sure you want to abort the current job?"):
                self.grbl.abort_stream()
                self._log("info", "Job aborted")

    # ------------------------------------------------------------------
    # Jog
    # ------------------------------------------------------------------
    def _do_jog(self, x, y, z, feed, home=False):
        if not self.grbl.is_connected:
            return
        if home:
            self.grbl.send_command("G0 X0 Y0 Z0")
        else:
            self.grbl.jog(x=x, y=y, z=z, feed=feed)

    # ------------------------------------------------------------------
    # Laser test
    # ------------------------------------------------------------------
    def _laser_test(self):
        if not self.grbl.is_connected:
            return
        # Brief low-power pulse (1% of S1000 = S10)
        self.grbl.send_command("M3 S10")
        self.root.after(500, lambda: self.grbl.send_command("M5 S0"))

    # ------------------------------------------------------------------
    # Manual command
    # ------------------------------------------------------------------
    def _send_manual(self, event=None):
        cmd = self._manual_entry.get().strip()
        if cmd and self.grbl.is_connected:
            self._log("tx", f">>> {cmd}")
            self.grbl.send_command(cmd)
            self._manual_entry.delete(0, "end")

    # ------------------------------------------------------------------
    # Console log
    # ------------------------------------------------------------------
    def _log(self, tag: str, text: str):
        """Thread-safe console logging."""
        def _do():
            self._console.insert("end", text + "\n", tag)
            self._console.see("end")
        self.root.after(0, _do)

    # ------------------------------------------------------------------
    # Callbacks from GrblController (called from worker threads)
    # ------------------------------------------------------------------
    def _on_status_change(self, status: MachineStatus):
        def _do():
            name = status.name
            self._status_text.set(name)
            color = self.STATUS_COLORS.get(status, "#888888")
            self._status_label.config(fg=color)
        self.root.after(0, _do)

    def _on_position_update(self):
        def _do():
            self._position_text.set(
                f"X: {self.grbl.work_x:8.3f}  Y: {self.grbl.work_y:8.3f}  Z: {self.grbl.work_z:8.3f}"
            )
        self.root.after(0, _do)

    def _on_progress_update(self, pct: float):
        def _do():
            self._progress_var.set(pct)
            if self._loaded_file:
                self._progress_text.set(
                    f"{self._loaded_file.ok_count} / {self._loaded_file.total}"
                )
                self.preview.set_progress(self._loaded_file.ok_count)
        self.root.after(0, _do)

    def _on_line_received(self, line: str):
        if not line.startswith("<"):  # Don't spam status reports
            self._log("rx", line)

    def _on_error(self, msg: str):
        self._log("error", msg)

    def _on_connected(self):
        def _do():
            self._connect_btn.config(text="Disconnect")
            ver = self.grbl.grbl_version or "unknown"
            self._log("info", f"Connected — GRBL {ver}")
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def _on_close(self):
        if self.grbl.is_connected:
            self.grbl.disconnect()
        self.root.destroy()

    def run(self):
        self.root.mainloop()

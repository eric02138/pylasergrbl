#!/usr/bin/env python3
"""PyLaserGRBL — Python laser engraver control for Raspberry Pi.

A Python port of LaserGRBL (https://github.com/arkypita/LaserGRBL)
designed to run on Raspberry Pi and Linux systems.

Usage:
    python main.py              # Launch GUI
    python main.py --headless   # Headless mode (stream a file)
    python main.py --help
"""

import sys
import argparse
import logging
import time


def main():
    parser = argparse.ArgumentParser(description="PyLaserGRBL — Laser Engraver Control")
    parser.add_argument("--headless", action="store_true",
                        help="Run in headless mode (no GUI)")
    parser.add_argument("--port", type=str, default="",
                        help="Serial port (e.g., /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200,
                        help="Baud rate (default: 115200)")
    parser.add_argument("--file", type=str, default="",
                        help="G-code file to stream (headless mode)")
    parser.add_argument("--image", type=str, default="",
                        help="Image file to convert and stream (headless mode)")
    parser.add_argument("--width", type=float, default=50,
                        help="Image width in mm (default: 50)")
    parser.add_argument("--power", type=int, default=1000,
                        help="Max laser power S value (default: 1000)")
    parser.add_argument("--feed", type=float, default=1000,
                        help="Feed rate mm/min (default: 1000)")
    parser.add_argument("--dpi", type=float, default=254,
                        help="Image resolution DPI (default: 254)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.headless:
        run_headless(args)
    else:
        run_gui()


def run_gui():
    """Launch the Tkinter GUI."""
    from gui.main_window import MainWindow
    app = MainWindow()
    app.run()


def run_headless(args):
    """Run in headless mode — stream a file without GUI."""
    from core.grbl_controller import GrblController
    from core.gcode_parser import GCodeFile
    from converters.image_to_gcode import image_to_gcode

    if not args.port:
        # Try to auto-detect
        from utils.serial_utils import list_serial_ports
        ports = list_serial_ports()
        if ports:
            args.port = ports[0]
            print(f"Auto-detected port: {args.port}")
        else:
            print("Error: No serial port found. Specify with --port")
            sys.exit(1)

    if not args.file and not args.image:
        print("Error: Specify --file or --image for headless mode")
        sys.exit(1)

    # Prepare G-code
    if args.image:
        print(f"Converting image: {args.image}")
        lines = image_to_gcode(
            image_path=args.image,
            resolution_dpi=args.dpi,
            feed_rate=args.feed,
            max_power=args.power,
            width_mm=args.width,
        )
        gcode_file = GCodeFile.from_lines(lines, filename=args.image)
    else:
        gcode_file = GCodeFile.from_file(args.file)

    print(f"Loaded {gcode_file.total} commands")

    # Connect and stream
    grbl = GrblController()

    finished = False

    def on_progress(pct):
        bar_len = 40
        filled = int(bar_len * pct / 100)
        bar = "=" * filled + "-" * (bar_len - filled)
        print(f"\r[{bar}] {pct:.1f}%  ({gcode_file.ok_count}/{gcode_file.total})", end="", flush=True)

    def on_finished():
        nonlocal finished
        finished = True
        print(f"\nJob complete: {gcode_file.ok_count}/{gcode_file.total} OK")

    def on_error(msg):
        print(f"\nError: {msg}")

    grbl.on_progress_update = on_progress
    grbl.on_job_finished = on_finished
    grbl.on_error = on_error

    print(f"Connecting to {args.port} @ {args.baud}...")
    grbl.connect(args.port, args.baud)

    if not grbl.is_connected:
        print("Failed to connect.")
        sys.exit(1)

    print(f"Connected (GRBL {grbl.grbl_version})")
    time.sleep(0.5)

    grbl.load_file(gcode_file)
    print("Starting stream...")
    grbl.start_stream()

    # Wait for completion
    try:
        while not finished:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nAborted by user")
        grbl.abort_stream()

    time.sleep(1)
    grbl.disconnect()
    print("Done.")


if __name__ == "__main__":
    main()

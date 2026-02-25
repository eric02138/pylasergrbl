#!/usr/bin/env python3
"""PyLaserGRBL — Python laser engraver control for Raspberry Pi.

A Python port of LaserGRBL (https://github.com/arkypita/LaserGRBL)
designed to run on Raspberry Pi and Linux systems.

Usage:
    python main.py                       # Launch GUI
    python main.py --headless --file job.gcode --port /dev/ttyUSB0
    python main.py --headless --image photo.jpg --width 50
    python main.py --headless --svg logo.svg --width 80 --passes 2
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
                        help="G-code file to stream")
    parser.add_argument("--image", type=str, default="",
                        help="Raster image file to convert and stream")
    parser.add_argument("--svg", type=str, default="",
                        help="SVG file to convert and stream")
    parser.add_argument("--width", type=float, default=50,
                        help="Target width in mm (default: 50)")
    parser.add_argument("--height", type=float, default=0,
                        help="Target height in mm (0 = proportional)")
    parser.add_argument("--power", type=int, default=1000,
                        help="Max laser power S value (default: 1000)")
    parser.add_argument("--feed", type=float, default=1000,
                        help="Cutting feed rate mm/min (default: 1000)")
    parser.add_argument("--travel-speed", type=float, default=3000,
                        help="Travel speed mm/min for SVG (default: 3000)")
    parser.add_argument("--passes", type=int, default=1,
                        help="Number of passes for SVG cutting (default: 1)")
    parser.add_argument("--dpi", type=float, default=254,
                        help="Image resolution DPI (default: 254)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")
    args = parser.parse_args()

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

    if not args.port:
        from utils.serial_utils import list_serial_ports
        ports = list_serial_ports()
        if ports:
            args.port = ports[0]
            print(f"Auto-detected port: {args.port}")
        else:
            print("Error: No serial port found. Specify with --port")
            sys.exit(1)

    if not args.file and not args.image and not args.svg:
        print("Error: Specify --file, --image, or --svg for headless mode")
        sys.exit(1)

    # Prepare G-code from the appropriate source
    if args.svg:
        from converters.svg_to_gcode import svg_to_gcode, SvgConvertSettings
        print(f"Converting SVG: {args.svg}")
        settings = SvgConvertSettings(
            feed_rate=args.feed,
            travel_speed=args.travel_speed,
            power=args.power,
            num_passes=args.passes,
            target_width_mm=args.width,
            target_height_mm=args.height if args.height > 0 else 0,
        )
        lines = svg_to_gcode(args.svg, settings=settings)
        gcode_file = GCodeFile.from_lines(lines, filename=args.svg)

    elif args.image:
        from converters.image_to_gcode import image_to_gcode
        print(f"Converting image: {args.image}")
        lines = image_to_gcode(
            image_path=args.image,
            resolution_dpi=args.dpi,
            feed_rate=args.feed,
            max_power=args.power,
            width_mm=args.width,
            height_mm=args.height if args.height > 0 else None,
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
        print(f"\r[{bar}] {pct:.1f}%  ({gcode_file.ok_count}/{gcode_file.total})",
              end="", flush=True)

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

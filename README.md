# PyLaserGRBL — Python LaserGRBL for Raspberry Pi

A Python port of the core functionality from [LaserGRBL](https://github.com/arkypita/LaserGRBL),
designed to run on Raspberry Pi (or any Linux system) with a Tkinter GUI.

## Features

- **Serial connection** to GRBL-based laser engravers (USB serial / CH340)
- **G-code streaming** with GRBL v1.1 flow control (character-counting protocol)
- **Image-to-GCode conversion**: raster line-by-line, dithering (Floyd-Steinberg, Ordered, Atkinson)
- **G-code file loading** with job preview
- **Jog controls** for manual positioning
- **Real-time status** display (position, state, progress)
- **Custom buttons** (Home, Unlock, Reset, Focus, etc.)
- **Configurable threading modes** (Slow / Quiet / Fast / UltraFast)
- **Lightweight Tkinter GUI** — no heavy desktop environment required

## Requirements

- Python 3.7+
- Raspberry Pi OS (or any Linux)
- USB connection to a GRBL-based laser engraver

## Installation

```bash
# Clone or copy this directory to your Pi
cd pylasergrbl

# Install dependencies
pip install pyserial Pillow numpy

# (Optional) For SVG support
pip install svgpathtools

# Run
python main.py
```

## Usage

1. Connect your laser engraver via USB
2. Run `python main.py`
3. Select the serial port (e.g., `/dev/ttyUSB0`) and baud rate (usually `115200`)
4. Click **Connect**
5. Load a G-code file or import an image
6. Click **Start** to begin the job

## Project Structure

```
pylasergrbl/
├── main.py                  # Entry point
├── core/
│   ├── __init__.py
│   ├── grbl_controller.py   # Serial communication & GRBL state machine
│   ├── gcode_parser.py      # G-code parsing and command management
│   └── streaming.py         # Character-counting G-code streamer
├── converters/
│   ├── __init__.py
│   ├── image_to_gcode.py    # Raster image → G-code (line-by-line, dithering)
│   └── svg_to_gcode.py      # SVG → G-code (basic)
├── gui/
│   ├── __init__.py
│   ├── main_window.py       # Main Tkinter GUI
│   ├── preview_canvas.py    # G-code path preview
│   └── jog_panel.py         # Manual jog controls
├── utils/
│   ├── __init__.py
│   └── serial_utils.py      # Port discovery helpers
├── requirements.txt
└── README.md
```

## Configuration

Edit `config.json` (auto-created on first run) or use the GUI settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `baud_rate` | 115200 | Serial baud rate |
| `threading_mode` | Fast | Slow / Quiet / Fast / UltraFast |
| `status_poll_ms` | 500 | Status query interval |
| `rx_buffer_size` | 128 | GRBL rx buffer (default for Arduino) |

## Credits

- Original [LaserGRBL](https://github.com/arkypita/LaserGRBL) by arkypita (GPLv3)
- This Python port is also released under GPLv3

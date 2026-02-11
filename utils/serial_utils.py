"""Serial port discovery utilities."""

import glob
import sys


def list_serial_ports():
    """List available serial ports on the system.

    Returns a list of port names (e.g., ['/dev/ttyUSB0', '/dev/ttyACM0']).
    Works on Linux (Raspberry Pi), macOS, and Windows.
    """
    if sys.platform.startswith("linux"):
        # Common patterns for USB-serial adapters on Linux/RPi
        patterns = [
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/ttyAMA*",
            "/dev/serial/by-id/*",
        ]
        ports = []
        for pattern in patterns:
            ports.extend(glob.glob(pattern))
        return sorted(set(ports))

    elif sys.platform.startswith("darwin"):
        return sorted(glob.glob("/dev/tty.usbserial*") + glob.glob("/dev/tty.usbmodem*"))

    elif sys.platform.startswith("win"):
        import serial.tools.list_ports
        return [p.device for p in serial.tools.list_ports.comports()]

    return []


COMMON_BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400]
DEFAULT_BAUD_RATE = 115200

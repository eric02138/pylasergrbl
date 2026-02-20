"""GRBL Controller — serial communication and state machine.

This is the Python equivalent of LaserGRBL's GrblCore.cs.
Handles connection, status polling, command sending, and GRBL state tracking.
"""

import re
import time
import threading
import logging
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Callable, List

import serial

from .gcode_parser import GCodeFile, GCodeCommand, CommandStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GRBL machine states (mirrors LaserGRBL MacStatus enum)
# ---------------------------------------------------------------------------
class MachineStatus(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    IDLE = auto()
    RUN = auto()
    JOG = auto()
    HOLD = auto()
    DOOR = auto()
    HOME = auto()
    ALARM = auto()
    CHECK = auto()
    UNKNOWN = auto()


# ---------------------------------------------------------------------------
# Threading modes (mirrors LaserGRBL ThreadingMode)
# ---------------------------------------------------------------------------
@dataclass
class ThreadingMode:
    status_query_ms: int
    tx_long_ms: int
    tx_short_ms: int
    rx_long_ms: int
    rx_short_ms: int
    name: str


THREADING_MODES = {
    "Slow":      ThreadingMode(2000, 15, 4, 2, 1, "Slow"),
    "Quiet":     ThreadingMode(1000, 10, 2, 1, 1, "Quiet"),
    "Fast":      ThreadingMode(500,  5,  1, 1, 0, "Fast"),
    "UltraFast": ThreadingMode(250,  1,  0, 1, 0, "UltraFast"),
}


# ---------------------------------------------------------------------------
# GRBL error / alarm code tables
# ---------------------------------------------------------------------------
GRBL_ERRORS = {
    1: "Expected command letter",
    2: "Bad number format",
    3: "Invalid $ statement",
    4: "Negative value",
    5: "Homing not enabled",
    6: "Step pulse too short",
    7: "EEPROM read fail",
    8: "Not idle",
    9: "G-code lock",
    10: "Soft limit",
    11: "Overflow",
    12: "Max step rate exceeded",
    13: "Check door",
    14: "Line length exceeded",
    15: "Travel exceeded",
    16: "Invalid jog command",
    17: "Laser mode requires PWM",
    20: "Unsupported command",
    21: "Modal group violation",
    22: "Undefined feed rate",
    23: "Invalid G-code ID",
    24: "Value word conflict",
    25: "Self-referencing arc",
    26: "No arc axis words",
    27: "Unused value words",
}

GRBL_ALARMS = {
    1: "Hard limit triggered",
    2: "Soft limit alarm",
    3: "Abort during cycle",
    4: "Probe fail — not cleared",
    5: "Probe fail — not contacted",
    6: "Homing fail — reset",
    7: "Homing fail — door",
    8: "Homing fail — pull off",
    9: "Homing fail — no switch",
}


# ---------------------------------------------------------------------------
# GRBL Controller
# ---------------------------------------------------------------------------
class GrblController:
    """Main GRBL controller — manages serial port and streams G-code.

    Mirrors the core logic from LaserGRBL's GrblCore.cs:
    - Connection / disconnection with DTR reset
    - Status polling via '?' command
    - Character-counting streaming protocol
    - Real-time command injection ($H, $X, ~, !, etc.)
    """

    RX_BUFFER_SIZE = 128  # GRBL default receive buffer size
    CONNECTION_TIMEOUT = 5

    def __init__(self):
        self._port: Optional[serial.Serial] = None
        self._status = MachineStatus.DISCONNECTED
        self._threading_mode = THREADING_MODES["Fast"]

        # Position tracking
        self.machine_x = 0.0
        self.machine_y = 0.0
        self.machine_z = 0.0
        self.work_x = 0.0
        self.work_y = 0.0
        self.work_z = 0.0

        # GRBL info
        self.grbl_version = ""
        self.grbl_options = ""
        self.feed_rate = 0.0
        self.spindle_speed = 0.0

        # Streaming state
        self._file: Optional[GCodeFile] = None
        self._streaming = False
        self._paused = False
        self._abort_flag = False
        self._cmd_index = 0
        self._buffer_fill: List[int] = []  # character counts of in-flight commands

        # Threads
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._alive = False

        # Callbacks
        self.on_status_change: Optional[Callable] = None
        self.on_position_update: Optional[Callable] = None
        self.on_progress_update: Optional[Callable] = None
        self.on_line_received: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_job_finished: Optional[Callable] = None

        self._lock = threading.Lock()
        self._rx_event = threading.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def status(self) -> MachineStatus:
        return self._status

    @status.setter
    def status(self, value: MachineStatus):
        if self._status != value:
            self._status = value
            if self.on_status_change:
                self.on_status_change(value)

    @property
    def is_connected(self) -> bool:
        return self._port is not None and self._port.is_open

    @property
    def is_streaming(self) -> bool:
        return self._streaming and not self._paused

    @property
    def is_idle(self) -> bool:
        return self.is_connected and self._status in (MachineStatus.IDLE, MachineStatus.ALARM)

    @property
    def progress(self) -> float:
        if self._file and self._file.total > 0:
            return self._file.ok_count / self._file.total * 100.0
        return 0.0

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self, port: str, baud: int = 115200):
        """Open serial connection to GRBL controller."""
        if self.is_connected:
            self.disconnect()

        self.status = MachineStatus.CONNECTING
        try:
            self._port = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.CONNECTION_TIMEOUT,
                write_timeout=5,
            )

            # Flush buffer
            time.sleep(2.0)  # Wait for GRBL to boot
            self._port.reset_output_buffer()

            self._alive = True
            self._start_threads()

            # Wait for GRBL welcome message (up to 10 seconds, like LaserGRBL)
            deadline = time.time() + 10
            while time.time() < deadline and not self.grbl_version:
                time.sleep(0.1)

            if self.grbl_version:
                self.status = MachineStatus.IDLE
                logger.info(f"Connected: GRBL {self.grbl_version}")
                if self.on_connected:
                    self.on_connected()
            else:
                # No welcome message — board was already running.
                # Send a status query and give the RX thread time to process it.
                logger.info("No welcome message — querying status directly")
                self._send_realtime(b"?")
                time.sleep(2)
                
                if self._status != MachineStatus.CONNECTING:
                    # RX thread parsed a status report and updated the state
                    logger.info(f"Connected via status query: {self._status.name}")
                else:
                    logger.warning("No GRBL welcome message or status response received")
                    self.status = MachineStatus.UNKNOWN
                
                if self.on_connected:
                    self.on_connected()

        except serial.SerialException as e:
            logger.error(f"Connection failed: {e}")
            self.status = MachineStatus.DISCONNECTED
            self._port = None
            if self.on_error:
                self.on_error(f"Connection failed: {e}")

    def disconnect(self):
        """Close serial connection."""
        self._alive = False
        self._streaming = False
        self._abort_flag = True

        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2)
        if self._tx_thread and self._tx_thread.is_alive():
            self._tx_thread.join(timeout=2)
        if self._status_thread and self._status_thread.is_alive():
            self._status_thread.join(timeout=2)

        if self._port and self._port.is_open:
            try:
                self._port.close()
            except Exception:
                pass

        self._port = None
        self.grbl_version = ""
        self.status = MachineStatus.DISCONNECTED
        if self.on_disconnected:
            self.on_disconnected()

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------
    def _start_threads(self):
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="grbl-rx")
        self._status_thread = threading.Thread(target=self._status_loop, daemon=True, name="grbl-status")
        self._rx_thread.start()
        self._status_thread.start()

    # ------------------------------------------------------------------
    # RX loop — reads lines from GRBL
    # ------------------------------------------------------------------
    def _rx_loop(self):
        """Continuously read lines from GRBL and process responses."""
        logger.info("RX thread started")
        while self._alive and self.is_connected:
            try:
                raw = self._port.readline()
                #logger.info(f"RX raw bytes: {repr(raw)}")
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                #logger.info(f"RX line: {line}")
                self._process_response(line)
            except serial.SerialException:
                if self._alive:
                    logger.error("Serial read error — disconnecting")
                    self._alive = False
                    self.status = MachineStatus.DISCONNECTED
                break
            except Exception as e:
                logger.debug(f"RX error: {e}")
        logger.info("RX thread exiting")

    def _process_response(self, line: str):
        """Process a single line received from GRBL."""
        if self.on_line_received:
            self.on_line_received(line)

        # Welcome message: "Grbl 1.1h ['$' for help]"
        m = re.match(r"Grbl\s+(\S+)", line, re.IGNORECASE)
        if m:
            self.grbl_version = m.group(1)
            logger.info(f"GRBL version: {self.grbl_version}")
            return

        # Status report: <Idle|MPos:0.000,0.000,0.000|...>
        if line.startswith("<") and line.endswith(">"):
            self._parse_status_report(line)
            return

        # ok response
        if line == "ok":
            self._handle_ok()
            return

        # error response
        m = re.match(r"error:(\d+)", line)
        if m:
            code = int(m.group(1))
            self._handle_error(code)
            return

        # ALARM
        m = re.match(r"ALARM:(\d+)", line)
        if m:
            alarm = int(m.group(1))
            self.status = MachineStatus.ALARM
            desc = GRBL_ALARMS.get(alarm, "Unknown alarm")
            logger.warning(f"ALARM:{alarm} — {desc}")
            if self.on_error:
                self.on_error(f"ALARM:{alarm} — {desc}")
            return

        # Settings echo [MSG: ...] or [$N=... ]
        logger.debug(f"GRBL: {line}")

    def _parse_status_report(self, line: str):
        """Parse GRBL v1.1 status report: <State|MPos:x,y,z|WPos:x,y,z|...>"""
        line = line.strip("<>")
        parts = line.split("|")

        if parts:
            state_str = parts[0].strip()
            state_map = {
                "Idle": MachineStatus.IDLE,
                "Run": MachineStatus.RUN,
                "Jog": MachineStatus.JOG,
                "Hold": MachineStatus.HOLD,
                "Door": MachineStatus.DOOR,
                "Home": MachineStatus.HOME,
                "Alarm": MachineStatus.ALARM,
                "Check": MachineStatus.CHECK,
            }
            # Handle sub-states like "Hold:0", "Door:1"
            base_state = state_str.split(":")[0]
            new_status = state_map.get(base_state, MachineStatus.UNKNOWN)
            self.status = new_status

        for part in parts[1:]:
            if part.startswith("MPos:"):
                coords = part[5:].split(",")
                if len(coords) >= 2:
                    self.machine_x = float(coords[0])
                    self.machine_y = float(coords[1])
                    self.machine_z = float(coords[2]) if len(coords) > 2 else 0.0
            elif part.startswith("WPos:"):
                coords = part[5:].split(",")
                if len(coords) >= 2:
                    self.work_x = float(coords[0])
                    self.work_y = float(coords[1])
                    self.work_z = float(coords[2]) if len(coords) > 2 else 0.0
            elif part.startswith("WCO:"):
                coords = part[4:].split(",")
                if len(coords) >= 2:
                    wco_x = float(coords[0])
                    wco_y = float(coords[1])
                    self.work_x = self.machine_x - wco_x
                    self.work_y = self.machine_y - wco_y
            elif part.startswith("FS:") or part.startswith("F:"):
                vals = part.split(":")[1].split(",")
                self.feed_rate = float(vals[0])
                if len(vals) > 1:
                    self.spindle_speed = float(vals[1])

        if self.on_position_update:
            self.on_position_update()

    # ------------------------------------------------------------------
    # OK / Error handling for streaming
    # ------------------------------------------------------------------
    def _handle_ok(self):
        with self._lock:
            if self._buffer_fill:
                self._buffer_fill.pop(0)
            if self._file and self._streaming:
                # Mark the oldest SENT command as OK
                for cmd in self._file.commands:
                    if cmd.status == CommandStatus.SENT:
                        cmd.status = CommandStatus.OK
                        break
                if self.on_progress_update:
                    self.on_progress_update(self.progress)
                self._rx_event.set()

    def _handle_error(self, code: int):
        desc = GRBL_ERRORS.get(code, "Unknown error")
        logger.warning(f"error:{code} — {desc}")
        with self._lock:
            if self._buffer_fill:
                self._buffer_fill.pop(0)
            if self._file and self._streaming:
                for cmd in self._file.commands:
                    if cmd.status == CommandStatus.SENT:
                        cmd.status = CommandStatus.ERROR
                        cmd.error_code = code
                        break
                self._rx_event.set()
        if self.on_error:
            self.on_error(f"error:{code} — {desc}")

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------
    def _status_loop(self):
        """Periodically send '?' to request status reports."""
        while self._alive and self.is_connected:
            try:
                self._send_realtime(b"?")
            except Exception:
                pass
            time.sleep(self._threading_mode.status_query_ms / 1000.0)

    # ------------------------------------------------------------------
    # Sending commands
    # ------------------------------------------------------------------
    def _send_realtime(self, data: bytes):
        """Send a real-time command (single byte, no newline, bypasses buffer)."""
        if self._port and self._port.is_open:
            self._port.write(data)

    def send_command(self, cmd_str: str):
        """Send a single G-code command (blocking until ack)."""
        logger.info("in send_command")
        if not self.is_connected:
            logger.error("Not Connected")
            return
        cmd_str = cmd_str.strip()
        if not cmd_str:
            logger.error("No cmd_str")
            return
        data = (cmd_str + "\n").encode("utf-8")
        logger.info(f"data: {data}")
        try:
            self._port.write(data)
            logger.info(f"TX: {cmd_str}")
        except serial.SerialException as e:
            logger.error(f"Send error: {e}")

    def send_immediate(self, cmd_str: str):
        """Send a command immediately (for real-time overrides and settings)."""
        self.send_command(cmd_str)

    # ------------------------------------------------------------------
    # Real-time GRBL commands
    # ------------------------------------------------------------------
    def soft_reset(self):
        """Send Ctrl-X soft reset."""
        self._send_realtime(b"\x18")
        self._streaming = False
        self._buffer_fill.clear()
        self.status = MachineStatus.IDLE

    def feed_hold(self):
        """Send feed hold '!'."""
        self._send_realtime(b"!")
        self._paused = True

    def cycle_resume(self):
        """Send cycle resume '~'."""
        self._send_realtime(b"~")
        self._paused = False

    def kill_alarm(self):
        """Send $X to kill alarm lock."""
        self.send_command("$X")

    def homing(self):
        """Send $H homing cycle."""
        self.send_command("$H")

    def jog(self, x: float = 0, y: float = 0, z: float = 0,
            feed: float = 1000, incremental: bool = True):
        """Send a jog command ($J=...)."""
        mode = "G91" if incremental else "G90"
        parts = [f"$J={mode}"]
        if x != 0:
            parts.append(f"X{x:.3f}")
        if y != 0:
            parts.append(f"Y{y:.3f}")
        if z != 0:
            parts.append(f"Z{z:.3f}")
        parts.append(f"F{feed:.0f}")
        self.send_command(" ".join(parts))

    def jog_cancel(self):
        """Cancel jog with 0x85."""
        self._send_realtime(b"\x85")

    def set_zero(self, x: bool = True, y: bool = True, z: bool = False):
        """Set current position as work coordinate zero."""
        axes = []
        if x:
            axes.append("X0")
        if y:
            axes.append("Y0")
        if z:
            axes.append("Z0")
        if axes:
            self.send_command(f"G92 {' '.join(axes)}")

    # ------------------------------------------------------------------
    # Streaming (character-counting protocol)
    # ------------------------------------------------------------------
    def load_file(self, gcode_file: GCodeFile):
        """Load a G-code file for streaming."""
        self._file = gcode_file
        self._file.reset_status()
        self._cmd_index = 0
        self._buffer_fill.clear()

    def start_stream(self):
        """Start streaming the loaded G-code file."""
        if not self._file or not self.is_connected:
            return
        if self._streaming:
            return

        self._file.reset_status()
        self._cmd_index = 0
        self._buffer_fill.clear()
        self._streaming = True
        self._paused = False
        self._abort_flag = False

        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True, name="grbl-tx")
        self._tx_thread.start()

    def pause_stream(self):
        self.feed_hold()

    def resume_stream(self):
        self.cycle_resume()

    def abort_stream(self):
        """Abort the current streaming job."""
        self._abort_flag = True
        self._streaming = False
        self.soft_reset()
        time.sleep(0.5)
        # Turn off laser for safety
        self.send_command("M5")
        self.send_command("G0 X0 Y0")

    def _tx_loop(self):
        """Streaming TX loop — character-counting protocol.

        This mirrors LaserGRBL's streaming logic:
        - Track how many bytes are in GRBL's RX buffer
        - Send commands as long as there's room
        - Wait for 'ok' / 'error' to free buffer space
        """
        logger.info("Streaming started")
        while self._streaming and self._alive and self._cmd_index < self._file.total:
            if self._abort_flag:
                break
            if self._paused:
                time.sleep(0.05)
                continue

            cmd = self._file.commands[self._cmd_index]
            byte_count = cmd.byte_count

            # Wait until there's room in GRBL's RX buffer
            with self._lock:
                buffer_used = sum(self._buffer_fill)

            while buffer_used + byte_count > self.RX_BUFFER_SIZE:
                if self._abort_flag or not self._alive:
                    break
                self._rx_event.wait(timeout=0.1)
                self._rx_event.clear()
                with self._lock:
                    buffer_used = sum(self._buffer_fill)

            if self._abort_flag or not self._alive:
                break

            # Send the command
            try:
                self._port.write(cmd.serial_bytes)
                with self._lock:
                    self._buffer_fill.append(byte_count)
                cmd.status = CommandStatus.SENT
                self._cmd_index += 1
                logger.debug(f"TX[{self._cmd_index}/{self._file.total}]: {cmd.stripped}")
            except serial.SerialException as e:
                logger.error(f"TX error: {e}")
                self._streaming = False
                break

            # Small sleep to prevent CPU spin (configurable via threading mode)
            sleep_ms = self._threading_mode.tx_short_ms
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)

        # Wait for all remaining acks
        if not self._abort_flag:
            deadline = time.time() + 30
            while self._buffer_fill and time.time() < deadline:
                time.sleep(0.1)

        self._streaming = False
        logger.info(f"Streaming finished: {self._file.ok_count}/{self._file.total} OK, "
                     f"{self._file.error_count} errors")
        if self.on_job_finished:
            self.on_job_finished()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def set_threading_mode(self, mode_name: str):
        if mode_name in THREADING_MODES:
            self._threading_mode = THREADING_MODES[mode_name]
            logger.info(f"Threading mode: {mode_name}")

    def request_settings(self):
        """Request GRBL settings ($$)."""
        self.send_command("$$")

    def request_parser_state(self):
        """Request G-code parser state ($G)."""
        self.send_command("$G")

    def request_build_info(self):
        """Request build info ($I)."""
        self.send_command("$I")

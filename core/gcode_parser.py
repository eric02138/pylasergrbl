"""G-code parsing and command representation.

Mirrors LaserGRBL's GrblCommand / GrblFile logic for parsing G-code lines,
extracting motion parameters, and building a command queue.
"""

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple


class CommandStatus(Enum):
    QUEUED = auto()
    SENT = auto()
    OK = auto()
    ERROR = auto()


@dataclass
class GCodeCommand:
    """A single G-code command with metadata."""
    raw: str                          # Original line text
    status: CommandStatus = CommandStatus.QUEUED
    error_code: Optional[int] = None

    @property
    def stripped(self) -> str:
        """Return the command with comments removed and whitespace trimmed."""
        # Remove inline comments (anything after ';' or inside parentheses)
        line = re.sub(r"\(.*?\)", "", self.raw)
        line = line.split(";")[0]
        return line.strip().upper()

    @property
    def is_empty(self) -> bool:
        return len(self.stripped) == 0

    @property
    def is_movement(self) -> bool:
        s = self.stripped
        return bool(re.match(r"^G[0-3]", s))

    @property
    def is_laser_on(self) -> bool:
        s = self.stripped
        return "M3" in s or "M4" in s

    @property
    def is_laser_off(self) -> bool:
        return "M5" in self.stripped

    def get_param(self, letter: str) -> Optional[float]:
        """Extract a numeric parameter (e.g., 'X', 'Y', 'S', 'F') from the line."""
        m = re.search(rf"{letter}(-?\d+\.?\d*)", self.stripped)
        if m:
            return float(m.group(1))
        return None

    @property
    def serial_bytes(self) -> bytes:
        """Bytes to send over serial (stripped line + newline)."""
        return (self.stripped + "\n").encode("ascii")

    @property
    def byte_count(self) -> int:
        return len(self.serial_bytes)

    def __repr__(self):
        return f"GCodeCommand({self.stripped!r}, {self.status.name})"


@dataclass
class GCodeFile:
    """A loaded G-code program (list of commands)."""
    commands: List[GCodeCommand] = field(default_factory=list)
    filename: str = ""

    @classmethod
    def from_file(cls, path: str) -> "GCodeFile":
        """Load a G-code file from disk."""
        commands = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    cmd = GCodeCommand(raw=line)
                    if not cmd.is_empty:
                        commands.append(cmd)
        return cls(commands=commands, filename=path)

    @classmethod
    def from_lines(cls, lines: List[str], filename: str = "<generated>") -> "GCodeFile":
        commands = []
        for line in lines:
            line = line.strip()
            if line:
                cmd = GCodeCommand(raw=line)
                if not cmd.is_empty:
                    commands.append(cmd)
        return cls(commands=commands, filename=filename)

    @property
    def total(self) -> int:
        return len(self.commands)

    @property
    def sent_count(self) -> int:
        return sum(1 for c in self.commands if c.status != CommandStatus.QUEUED)

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.commands if c.status == CommandStatus.OK)

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.commands if c.status == CommandStatus.ERROR)

    def reset_status(self):
        for cmd in self.commands:
            cmd.status = CommandStatus.QUEUED
            cmd.error_code = None

    def get_bounds(self) -> Tuple[float, float, float, float]:
        """Compute the bounding box (min_x, min_y, max_x, max_y) of all motion commands."""
        x, y = 0.0, 0.0
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")

        for cmd in self.commands:
            nx = cmd.get_param("X")
            ny = cmd.get_param("Y")
            if nx is not None:
                x = nx
            if ny is not None:
                y = ny
            if cmd.is_movement:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

        if min_x == float("inf"):
            return (0, 0, 0, 0)
        return (min_x, min_y, max_x, max_y)

    def get_toolpath(self) -> List[Tuple[float, float, bool]]:
        """Extract a list of (x, y, laser_on) points for preview rendering."""
        points = []
        x, y = 0.0, 0.0
        laser_on = False

        for cmd in self.commands:
            s = cmd.stripped
            if cmd.is_laser_on:
                laser_on = True
            elif cmd.is_laser_off:
                laser_on = False

            nx = cmd.get_param("X")
            ny = cmd.get_param("Y")
            if nx is not None:
                x = nx
            if ny is not None:
                y = ny

            if cmd.is_movement:
                power = cmd.get_param("S")
                is_cutting = laser_on or (power is not None and power > 0)
                points.append((x, y, is_cutting))

        return points

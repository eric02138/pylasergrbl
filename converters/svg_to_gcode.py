"""SVG to G-code conversion.

Converts SVG vector paths to G-code for laser cutting/engraving.
Supports:
- Lines, arcs, cubic/quadratic Bezier curves
- Auto-scaling to target dimensions
- Configurable cut power, travel speed, number of passes
- Path optimization (nearest-neighbor ordering)
- Coordinate flipping (SVG Y is inverted vs CNC Y)

Requires: pip install svgpathtools
Also includes a pure-Python fallback parser for simple SVGs when
svgpathtools is not installed.
"""

import logging
import math
import re
import xml.etree.ElementTree as ET
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

try:
    from svgpathtools import (
        svg2paths2, Line, Arc, CubicBezier, QuadraticBezier, Path
    )
    HAS_SVGPATHTOOLS = True
except ImportError:
    HAS_SVGPATHTOOLS = False


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass
class SvgConvertSettings:
    """All settings for SVG → G-code conversion."""
    feed_rate: float = 500          # Cutting feed mm/min
    travel_speed: float = 3000      # Rapid move speed mm/min
    power: int = 1000               # Laser power S value for cutting
    min_power: int = 0              # Laser power for travel moves
    num_passes: int = 1             # Number of repeated passes (for cutting)
    target_width_mm: float = 0      # Target width (0 = use SVG native units)
    target_height_mm: float = 0     # Target height (0 = use SVG native units)
    offset_x: float = 0.0          # X origin offset
    offset_y: float = 0.0          # Y origin offset
    bezier_resolution: int = 20     # Line segments per Bezier curve
    laser_mode: bool = True         # M4 dynamic (True) or M3 constant (False)
    flip_y: bool = True             # Flip Y axis (SVG Y goes down, CNC Y goes up)
    optimize_paths: bool = True     # Reorder paths by nearest-neighbor
    merge_tolerance: float = 0.05   # mm — snap start of path to end of previous


# ---------------------------------------------------------------------------
# Main conversion function
# ---------------------------------------------------------------------------

def svg_to_gcode(
    svg_path: str,
    settings: Optional[SvgConvertSettings] = None,
    **kwargs,
) -> List[str]:
    """Convert an SVG file to G-code lines.

    Can be called two ways:
        svg_to_gcode("file.svg", settings=SvgConvertSettings(...))
        svg_to_gcode("file.svg", feed_rate=500, power=800, ...)

    Returns a list of G-code string lines.
    """
    if settings is None:
        settings = SvgConvertSettings(**kwargs)

    # --- Parse SVG paths ---
    raw_paths = _parse_svg(svg_path, settings.bezier_resolution)

    if not raw_paths:
        return ["; WARNING: No paths found in SVG file", "; Check that the file contains <path> elements"]

    # --- Compute bounding box of all paths ---
    all_points = [pt for path in raw_paths for pt in path]
    min_x = min(p[0] for p in all_points)
    min_y = min(p[1] for p in all_points)
    max_x = max(p[0] for p in all_points)
    max_y = max(p[1] for p in all_points)

    svg_w = max_x - min_x
    svg_h = max_y - min_y

    # --- Compute scale & offset ---
    scale = 1.0
    if settings.target_width_mm > 0 and settings.target_height_mm > 0:
        sx = settings.target_width_mm / svg_w if svg_w > 0 else 1.0
        sy = settings.target_height_mm / svg_h if svg_h > 0 else 1.0
        scale = min(sx, sy)
    elif settings.target_width_mm > 0:
        scale = settings.target_width_mm / svg_w if svg_w > 0 else 1.0
    elif settings.target_height_mm > 0:
        scale = settings.target_height_mm / svg_h if svg_h > 0 else 1.0

    final_w = svg_w * scale
    final_h = svg_h * scale

    # Transform all paths: shift to origin, scale, optional Y flip, add offset
    transformed = []
    for path in raw_paths:
        new_path = []
        for x, y in path:
            nx = (x - min_x) * scale + settings.offset_x
            ny = (y - min_y) * scale
            if settings.flip_y:
                ny = final_h - ny  # Flip so Y=0 is bottom
            ny += settings.offset_y
            new_path.append((nx, ny))
        if len(new_path) >= 2:
            transformed.append(new_path)

    # --- Optimize path order (nearest-neighbor) ---
    if settings.optimize_paths and len(transformed) > 1:
        transformed = _optimize_path_order(transformed)

    # --- Generate G-code ---
    gcode: List[str] = []
    gcode.append(f"; SVG to G-code — PyLaserGRBL")
    gcode.append(f"; Source: {svg_path}")
    gcode.append(f"; Size: {final_w:.1f} x {final_h:.1f} mm  (scale: {scale:.4f})")
    gcode.append(f"; Paths: {len(transformed)}, Passes: {settings.num_passes}")
    gcode.append("")
    gcode.append("G90")              # Absolute positioning
    gcode.append("G21")              # Millimeters
    laser_cmd = "M4" if settings.laser_mode else "M3"
    gcode.append(f"{laser_cmd} S0")  # Laser on, power 0

    for pass_num in range(settings.num_passes):
        if settings.num_passes > 1:
            gcode.append(f"; --- Pass {pass_num + 1}/{settings.num_passes} ---")

        for path in transformed:
            if len(path) < 2:
                continue

            # Rapid move to start of path (laser off)
            sx, sy = path[0]
            gcode.append(f"G0 X{sx:.4f} Y{sy:.4f} S{settings.min_power}")

            # Cut along the path
            for x, y in path[1:]:
                gcode.append(f"G1 X{x:.4f} Y{y:.4f} S{settings.power} F{settings.feed_rate:.0f}")

            # Laser off after each path
            gcode.append(f"G0 S{settings.min_power}")

    gcode.append("M5")              # Laser off
    gcode.append("G0 X0 Y0")       # Return to origin
    gcode.append(f"; End — {sum(len(p) for p in transformed)} points across {len(transformed)} paths")
    return gcode


# ---------------------------------------------------------------------------
# SVG parsing
# ---------------------------------------------------------------------------

def _parse_svg(svg_path: str, bezier_res: int) -> List[List[Tuple[float, float]]]:
    """Parse SVG and return a list of polyline paths (each is a list of (x,y) points).

    Uses svgpathtools for <path> elements if available, and always uses
    the fallback parser for basic shapes (<rect>, <circle>, <ellipse>,
    <line>, <polyline>, <polygon>) since svgpathtools ignores those.
    """
    # DEBUG — remove later
    # print(f"DEBUG _parse_svg called with: {svg_path}")
    # print(f"DEBUG HAS_SVGPATHTOOLS: {HAS_SVGPATHTOOLS}")
    # for local, elem in _iter_svg_elements(svg_path):
    #     print(f"DEBUG found element: <{local}> attribs={dict(elem.attrib)}")


    path_results = []
    shape_results = []

    # Parse <path> elements
    if HAS_SVGPATHTOOLS:
        try:
            path_results = _parse_with_svgpathtools(svg_path, bezier_res)
        except Exception as e:
            # If svgpathtools fails, fall through to fallback for paths too
            logging.getLogger(__name__).warning(f"svgpathtools failed: {e}, using fallback")
            return _parse_fallback(svg_path, bezier_res)

    # Always parse basic shapes with fallback (svgpathtools skips them)
    shape_results = _parse_fallback_shapes_only(svg_path, bezier_res)

    # If svgpathtools wasn't available, use fallback for paths too
    if not HAS_SVGPATHTOOLS:
        path_results = _parse_fallback_paths_only(svg_path, bezier_res)

    return path_results + shape_results


def _parse_with_svgpathtools(svg_path: str, bezier_res: int) -> List[List[Tuple[float, float]]]:
    """Parse SVG using svgpathtools library."""
    paths, attributes, svg_attribs = svg2paths2(svg_path)
    result = []

    for path in paths:
        if len(path) == 0:
            continue
        points: List[Tuple[float, float]] = []

        # Add start point of the first segment
        start = path[0].start
        points.append((start.real, start.imag))

        for segment in path:
            if isinstance(segment, Line):
                end = segment.end
                points.append((end.real, end.imag))

            elif isinstance(segment, (CubicBezier, QuadraticBezier)):
                for i in range(1, bezier_res + 1):
                    t = i / bezier_res
                    pt = segment.point(t)
                    points.append((pt.real, pt.imag))

            elif isinstance(segment, Arc):
                for i in range(1, bezier_res + 1):
                    t = i / bezier_res
                    pt = segment.point(t)
                    points.append((pt.real, pt.imag))

        # Remove duplicate consecutive points
        cleaned = [points[0]]
        for pt in points[1:]:
            if abs(pt[0] - cleaned[-1][0]) > 0.001 or abs(pt[1] - cleaned[-1][1]) > 0.001:
                cleaned.append(pt)

        if len(cleaned) >= 2:
            result.append(cleaned)

    return result


def _parse_fallback(svg_path: str, bezier_res: int) -> List[List[Tuple[float, float]]]:
    """Fallback SVG parser using xml.etree — handles basic shapes and paths.

    Supports: <path>, <line>, <polyline>, <polygon>, <rect>, <circle>, <ellipse>
    """
    return _parse_fallback_paths_only(svg_path, bezier_res) + \
           _parse_fallback_shapes_only(svg_path, bezier_res)


def _iter_svg_elements(svg_path: str):
    """Yield (local_tag, element) for all elements in an SVG file."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    for elem in root.iter():
        local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        yield local, elem


def _parse_fallback_paths_only(svg_path: str, bezier_res: int) -> List[List[Tuple[float, float]]]:
    """Parse only <path> elements using the fallback parser."""
    result = []
    for local, elem in _iter_svg_elements(svg_path):
        if local == "path":
            d = elem.get("d", "")
            if d.strip():
                paths = _parse_svg_d(d, bezier_res)
                result.extend(paths)
    return result


def _parse_fallback_shapes_only(svg_path: str, bezier_res: int) -> List[List[Tuple[float, float]]]:
    """Parse only basic shape elements: <line>, <polyline>, <polygon>, <rect>, <circle>, <ellipse>."""
    result = []
    for local, elem in _iter_svg_elements(svg_path):
        if local == "line":
            x1 = float(elem.get("x1", 0))
            y1 = float(elem.get("y1", 0))
            x2 = float(elem.get("x2", 0))
            y2 = float(elem.get("y2", 0))
            result.append([(x1, y1), (x2, y2)])

        elif local == "polyline":
            pts = _parse_points(elem.get("points", ""))
            if len(pts) >= 2:
                result.append(pts)

        elif local == "polygon":
            pts = _parse_points(elem.get("points", ""))
            if len(pts) >= 2:
                pts.append(pts[0])
                result.append(pts)

        elif local == "rect":
            x = float(elem.get("x", 0))
            y = float(elem.get("y", 0))
            w = float(elem.get("width", 0))
            h = float(elem.get("height", 0))
            if w > 0 and h > 0:
                result.append([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)])

        elif local == "circle":
            cx = float(elem.get("cx", 0))
            cy = float(elem.get("cy", 0))
            r = float(elem.get("r", 0))
            if r > 0:
                pts = []
                steps = max(bezier_res, 36)
                for i in range(steps + 1):
                    angle = 2 * math.pi * i / steps
                    pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
                result.append(pts)

        elif local == "ellipse":
            cx = float(elem.get("cx", 0))
            cy = float(elem.get("cy", 0))
            rx = float(elem.get("rx", 0))
            ry = float(elem.get("ry", 0))
            if rx > 0 and ry > 0:
                pts = []
                steps = max(bezier_res, 36)
                for i in range(steps + 1):
                    angle = 2 * math.pi * i / steps
                    pts.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
                result.append(pts)

    return result

def _parse_points(s: str) -> List[Tuple[float, float]]:
    """Parse SVG points attribute: '10,20 30,40 50,60'"""
    pts = []
    pairs = re.findall(r"(-?\d+\.?\d*)\s*[,\s]\s*(-?\d+\.?\d*)", s)
    for x, y in pairs:
        pts.append((float(x), float(y)))
    return pts


def _parse_svg_d(d: str, bezier_res: int) -> List[List[Tuple[float, float]]]:
    """Parse SVG path 'd' attribute into polyline paths.

    Supports: M, L, H, V, C, S, Q, T, A, Z (uppercase=absolute, lowercase=relative)
    """
    # Tokenize
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)

    paths = []
    current_path: List[Tuple[float, float]] = []
    x, y = 0.0, 0.0        # Current point
    sx, sy = 0.0, 0.0      # Subpath start
    last_ctrl = None        # Last control point for smooth curves
    cmd = ""
    i = 0

    def next_float():
        nonlocal i
        if i < len(tokens):
            val = float(tokens[i])
            i += 1
            return val
        return 0.0

    while i < len(tokens):
        token = tokens[i]

        if token.isalpha():
            cmd = token
            i += 1
        else:
            # Implicit repeat of previous command (L after M, etc.)
            pass

        if cmd in ("M", "m"):
            # Start new subpath
            if current_path and len(current_path) >= 2:
                paths.append(current_path)
            nx, ny = next_float(), next_float()
            if cmd == "m":
                x, y = x + nx, y + ny
            else:
                x, y = nx, ny
            sx, sy = x, y
            current_path = [(x, y)]
            cmd = "L" if cmd == "M" else "l"  # Implicit lineto after moveto

        elif cmd in ("L", "l"):
            nx, ny = next_float(), next_float()
            if cmd == "l":
                x, y = x + nx, y + ny
            else:
                x, y = nx, ny
            current_path.append((x, y))

        elif cmd in ("H", "h"):
            nx = next_float()
            x = x + nx if cmd == "h" else nx
            current_path.append((x, y))

        elif cmd in ("V", "v"):
            ny = next_float()
            y = y + ny if cmd == "v" else ny
            current_path.append((x, y))

        elif cmd in ("C", "c"):
            # Cubic bezier: control1, control2, end
            x1, y1 = next_float(), next_float()
            x2, y2 = next_float(), next_float()
            ex, ey = next_float(), next_float()
            if cmd == "c":
                x1, y1 = x + x1, y + y1
                x2, y2 = x + x2, y + y2
                ex, ey = x + ex, y + ey
            for t_i in range(1, bezier_res + 1):
                t = t_i / bezier_res
                u = 1 - t
                px = u**3 * x + 3*u**2*t * x1 + 3*u*t**2 * x2 + t**3 * ex
                py = u**3 * y + 3*u**2*t * y1 + 3*u*t**2 * y2 + t**3 * ey
                current_path.append((px, py))
            last_ctrl = (x2, y2)
            x, y = ex, ey

        elif cmd in ("S", "s"):
            # Smooth cubic: reflected control1, control2, end
            x2, y2 = next_float(), next_float()
            ex, ey = next_float(), next_float()
            if cmd == "s":
                x2, y2 = x + x2, y + y2
                ex, ey = x + ex, y + ey
            if last_ctrl:
                x1 = 2 * x - last_ctrl[0]
                y1 = 2 * y - last_ctrl[1]
            else:
                x1, y1 = x, y
            for t_i in range(1, bezier_res + 1):
                t = t_i / bezier_res
                u = 1 - t
                px = u**3 * x + 3*u**2*t * x1 + 3*u*t**2 * x2 + t**3 * ex
                py = u**3 * y + 3*u**2*t * y1 + 3*u*t**2 * y2 + t**3 * ey
                current_path.append((px, py))
            last_ctrl = (x2, y2)
            x, y = ex, ey

        elif cmd in ("Q", "q"):
            # Quadratic bezier
            cx_, cy_ = next_float(), next_float()
            ex, ey = next_float(), next_float()
            if cmd == "q":
                cx_, cy_ = x + cx_, y + cy_
                ex, ey = x + ex, y + ey
            for t_i in range(1, bezier_res + 1):
                t = t_i / bezier_res
                u = 1 - t
                px = u**2 * x + 2*u*t * cx_ + t**2 * ex
                py = u**2 * y + 2*u*t * cy_ + t**2 * ey
                current_path.append((px, py))
            last_ctrl = (cx_, cy_)
            x, y = ex, ey

        elif cmd in ("T", "t"):
            # Smooth quadratic
            ex, ey = next_float(), next_float()
            if cmd == "t":
                ex, ey = x + ex, y + ey
            if last_ctrl:
                cx_ = 2 * x - last_ctrl[0]
                cy_ = 2 * y - last_ctrl[1]
            else:
                cx_, cy_ = x, y
            for t_i in range(1, bezier_res + 1):
                t = t_i / bezier_res
                u = 1 - t
                px = u**2 * x + 2*u*t * cx_ + t**2 * ex
                py = u**2 * y + 2*u*t * cy_ + t**2 * ey
                current_path.append((px, py))
            last_ctrl = (cx_, cy_)
            x, y = ex, ey

        elif cmd in ("A", "a"):
            # Elliptical arc — approximate with line segments
            rx_ = next_float()
            ry_ = next_float()
            rot = next_float()
            large = int(next_float())
            sweep = int(next_float())
            ex, ey = next_float(), next_float()
            if cmd == "a":
                ex, ey = x + ex, y + ey
            arc_pts = _approximate_arc(x, y, ex, ey, rx_, ry_, rot, large, sweep, bezier_res)
            current_path.extend(arc_pts)
            x, y = ex, ey

        elif cmd in ("Z", "z"):
            if current_path:
                current_path.append((sx, sy))
            x, y = sx, sy
            if current_path and len(current_path) >= 2:
                paths.append(current_path)
            current_path = [(x, y)]

        else:
            i += 1  # Skip unknown

    if current_path and len(current_path) >= 2:
        paths.append(current_path)

    return paths


def _approximate_arc(x1, y1, x2, y2, rx, ry, phi_deg, fa, fs, steps):
    """Approximate an SVG elliptical arc with line segments."""
    if rx == 0 or ry == 0:
        return [(x2, y2)]

    phi = math.radians(phi_deg)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    # Step 1: Compute (x1', y1')
    dx = (x1 - x2) / 2
    dy = (y1 - y2) / 2
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    # Step 2: Compute (cx', cy')
    rx2, ry2 = rx * rx, ry * ry
    x1p2, y1p2 = x1p * x1p, y1p * y1p

    # Ensure radii are large enough
    lam = x1p2 / rx2 + y1p2 / ry2
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
        rx2, ry2 = rx * rx, ry * ry

    num = max(rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2, 0)
    den = rx2 * y1p2 + ry2 * x1p2
    sq = math.sqrt(num / den) if den > 0 else 0

    if fa == fs:
        sq = -sq

    cxp = sq * rx * y1p / ry
    cyp = -sq * ry * x1p / rx

    # Step 3: Compute (cx, cy) from (cx', cy')
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2

    # Step 4: Compute start and sweep angles
    def angle(ux, uy, vx, vy):
        n = math.sqrt(ux*ux + uy*uy) * math.sqrt(vx*vx + vy*vy)
        if n == 0:
            return 0
        c = (ux * vx + uy * vy) / n
        c = max(-1, min(1, c))
        a = math.acos(c)
        if ux * vy - uy * vx < 0:
            a = -a
        return a

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle(
        (x1p - cxp) / rx, (y1p - cyp) / ry,
        (-x1p - cxp) / rx, (-y1p - cyp) / ry
    )

    if fs == 0 and dtheta > 0:
        dtheta -= 2 * math.pi
    elif fs == 1 and dtheta < 0:
        dtheta += 2 * math.pi

    # Generate points
    pts = []
    for i in range(1, steps + 1):
        t = theta1 + dtheta * i / steps
        ex = rx * math.cos(t)
        ey = ry * math.sin(t)
        px = cos_phi * ex - sin_phi * ey + cx
        py = sin_phi * ex + cos_phi * ey + cy
        pts.append((px, py))

    return pts


# ---------------------------------------------------------------------------
# Path optimization
# ---------------------------------------------------------------------------

def _optimize_path_order(paths: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
    """Reorder paths using nearest-neighbor heuristic to minimize travel.

    Also considers reversing paths if that brings the start closer.
    """
    if len(paths) <= 1:
        return paths

    remaining = list(range(len(paths)))
    ordered = []

    # Start from path closest to origin
    current = (0.0, 0.0)

    while remaining:
        best_idx = -1
        best_dist = float("inf")
        best_reversed = False

        for idx in remaining:
            path = paths[idx]
            start = path[0]
            end = path[-1]

            d_start = _dist(current, start)
            d_end = _dist(current, end)

            if d_start < best_dist:
                best_dist = d_start
                best_idx = idx
                best_reversed = False

            if d_end < best_dist:
                best_dist = d_end
                best_idx = idx
                best_reversed = True

        p = paths[best_idx]
        if best_reversed:
            p = list(reversed(p))

        ordered.append(p)
        current = p[-1]
        remaining.remove(best_idx)

    return ordered


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0])**2 + (a[1] - b[1])**2)


# ---------------------------------------------------------------------------
# Utility: get SVG info without full conversion
# ---------------------------------------------------------------------------

def get_svg_info(svg_path: str) -> dict:
    """Return basic info about an SVG file (dimensions, path count) for the UI."""
    try:
        logger.info(f"svg_path: {svg_path}")
        tree = ET.parse(svg_path)
        root = tree.getroot()
        logger.error(f"root: {root}")

        width = root.get("width", "")
        height = root.get("height", "")
        viewbox = root.get("viewBox", "")

        # Try to parse dimensions
        w_mm = _parse_svg_length(width)
        h_mm = _parse_svg_length(height)

        if not w_mm and viewbox:
            parts = viewbox.split()
            if len(parts) == 4:
                w_mm = float(parts[2])
                h_mm = float(parts[3])

        # Count paths
        path_count = 0
        for elem in root.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local in ("path", "line", "polyline", "polygon", "rect", "circle", "ellipse"):
                path_count += 1

        return {
            "width": w_mm or 0,
            "height": h_mm or 0,
            "width_str": width,
            "height_str": height,
            "viewbox": viewbox,
            "path_count": path_count,
        }
    except Exception as e:
        return {"error": str(e)}


def _parse_svg_length(s: str) -> Optional[float]:
    """Parse an SVG length string like '100mm', '5in', '200px', '50'."""
    if not s:
        return None
    m = re.match(r"([\d.]+)\s*(mm|cm|in|pt|px|)?$", s.strip())
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "mm":
        return val
    elif unit == "cm":
        return val * 10
    elif unit == "in":
        return val * 25.4
    elif unit == "pt":
        return val * 25.4 / 72
    elif unit == "px" or unit == "":
        return val  # Treat as user units (≈px)
    return val

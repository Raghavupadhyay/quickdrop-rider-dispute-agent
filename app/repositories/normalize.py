"""Cleaning rules for the exports "as they came" from the ops systems.

Everything here is a correction we can defend from the data itself; each fix
is recorded on the row so it shows up in the trace.

  * rider ids come in several spellings ("R7", "r19", "R007") -> "R007"
  * one city's trips system exports distance in metres (800 .. 11400) while the
    others use km (0.8 .. 11.4). No delivery trip is 100+ km, so anything at or
    above 100 is read as metres.
"""

import re


RIDER_ID_RE = re.compile(r"^\s*r\s*0*(\d+)\s*$", re.IGNORECASE)

# Above this a "km" value can only be metres.
METRES_THRESHOLD_KM = 100.0


def normalize_rider_id(value: str) -> str:
    match = RIDER_ID_RE.match(value or "")
    if not match:
        return (value or "").strip().upper()
    return f"R{int(match.group(1)):03d}"


def normalize_distance_km(value: float) -> tuple[float, str | None]:
    """Returns (distance_km, note). note is None when nothing was changed."""
    if value >= METRES_THRESHOLD_KM:
        fixed = round(value / 1000.0, 3)
        return fixed, f"distance {value:g} read as {fixed:g} km (export in metres)"
    return value, None

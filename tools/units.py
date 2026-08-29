"""Strict unit parsing for scenario files."""

from __future__ import annotations

import re


class UnitError(ValueError):
    pass


_NUMBER_UNIT = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([A-Za-z]+)$")


def _parse(value: object, units: dict[str, float], kind: str) -> float:
    if not isinstance(value, str):
        raise UnitError(f"{kind} must be a number with an explicit unit, got {value!r}")
    match = _NUMBER_UNIT.fullmatch(value.strip())
    if not match or match.group(2) not in units:
        supported = ", ".join(units)
        raise UnitError(f"unsupported {kind} value {value!r}; supported units: {supported}")
    result = float(match.group(1)) * units[match.group(2)]
    if result <= 0:
        raise UnitError(f"{kind} must be greater than zero, got {value!r}")
    return result


def parse_time(value: object, *, allow_zero: bool = False) -> float:
    if allow_zero and isinstance(value, str) and re.fullmatch(r"(?:0+(?:\.0*)?|\.0+)\s*(?:ns|us|ms|s)", value.strip()):
        return 0.0
    return _parse(value, {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}, "time")


def parse_bytes(value: object) -> int:
    parsed = _parse(value, {"B": 1.0, "KB": 1000.0}, "data size")
    if not parsed.is_integer():
        raise UnitError(f"data size must resolve to a whole byte count, got {value!r}")
    return int(parsed)


def parse_bitrate(value: object) -> float:
    return _parse(value, {"Kbps": 1e3, "Mbps": 1e6, "Gbps": 1e9}, "bitrate")


def seconds_text(value: float) -> str:
    return f"{value:.12g}s"


def bitrate_text(value: float) -> str:
    return f"{value:.12g}bps"

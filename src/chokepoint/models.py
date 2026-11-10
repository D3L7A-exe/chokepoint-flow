"""Normalized data model shared by all collectors, storage backends and analysis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Flow:
    """One unidirectional flow record.

    `start` and `end` are Unix timestamps (seconds). `exporter` is the IP address of
    the device that sent the record, and `in_if`/`out_if` are its interface indexes.
    """

    exporter: str
    src: str
    dst: str
    sport: int
    dport: int
    proto: int
    bytes: int
    packets: int
    start: float
    end: float
    in_if: int = 0
    out_if: int = 0


@dataclass(frozen=True, slots=True)
class LinkConfig:
    """A monitored link: one interface on one exporter, with its capacity."""

    name: str
    exporter: str
    interface: int
    capacity_mbps: float


@dataclass(frozen=True, slots=True)
class LinkStatus:
    """Utilization of a link over an analysis window."""

    link: LinkConfig
    bytes: int
    window_seconds: float
    mbps: float
    utilization: float  # 0.0 - 1.0+
    level: str  # "ok" | "warning" | "critical"

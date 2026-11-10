"""Bottleneck detection from link utilization."""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

from ..models import LinkConfig, LinkStatus
from ..storage.base import FlowStore

WARNING = 0.70
CRITICAL = 0.90


def classify(utilization: float, warning: float = WARNING, critical: float = CRITICAL) -> str:
    if utilization >= critical:
        return "critical"
    if utilization >= warning:
        return "warning"
    return "ok"


def load_links(path: str | Path) -> list[LinkConfig]:
    """Read link definitions from a TOML file (`[[link]]` tables)."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    links = []
    for entry in data.get("link", []):
        links.append(
            LinkConfig(
                name=entry["name"],
                exporter=entry["exporter"],
                interface=int(entry["interface"]),
                capacity_mbps=float(entry["capacity_mbps"]),
            )
        )
    return links


def analyze_links(
    store: FlowStore,
    links: list[LinkConfig],
    window_seconds: float = 300,
    now: float | None = None,
    warning: float = WARNING,
    critical: float = CRITICAL,
) -> list[LinkStatus]:
    """Compute throughput and utilization per link, most loaded first."""
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    now = time.time() if now is None else now
    since = now - window_seconds
    results = []
    for link in links:
        total = store.link_bytes(link.exporter, link.interface, since)
        mbps = total * 8 / window_seconds / 1e6
        util = mbps / link.capacity_mbps if link.capacity_mbps > 0 else 0.0
        results.append(
            LinkStatus(link=link, bytes=total, window_seconds=window_seconds, mbps=mbps,
                       utilization=util, level=classify(util, warning, critical))
        )
    return sorted(results, key=lambda s: s.utilization, reverse=True)

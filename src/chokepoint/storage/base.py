"""Storage interface. Backends (SQLite now, ClickHouse/TimescaleDB later) implement this."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from ..models import Flow


class FlowStore(Protocol):
    def add_flows(self, flows: Iterable[Flow]) -> None: ...

    def top_talkers(self, since: float, limit: int = 10) -> list[tuple[str, int]]:
        """Return (src_ip, total_bytes) ordered by bytes, for flows ending after `since`."""
        ...

    def top_flows(self, since: float, limit: int = 10) -> list[tuple[str, str, int, int]]:
        """Return (src, dst, dport, total_bytes) ordered by bytes."""
        ...

    def link_bytes(self, exporter: str, interface: int, since: float) -> int:
        """Total bytes leaving `interface` on `exporter` for flows ending after `since`."""
        ...

    def graph_edges(self, since: float, limit: int = 1000) -> list[tuple[str, str, int, int]]:
        """Return (src, dst, total_bytes, total_packets) per host pair, largest first."""
        ...

    def timeline(self, since: float, bucket_seconds: float) -> list[tuple[float, int]]:
        """Return (bucket_start_timestamp, total_bytes) ordered by time."""
        ...

    def totals(self, since: float) -> tuple[int, int]:
        """Return (flow_count, total_bytes) for flows ending after `since`."""
        ...

    def close(self) -> None: ...

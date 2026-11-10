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

    def close(self) -> None: ...

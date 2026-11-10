"""SQLite backend: good for homelab and small networks."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable

from ..models import Flow

SCHEMA = """
CREATE TABLE IF NOT EXISTS flows (
    exporter TEXT NOT NULL,
    src      TEXT NOT NULL,
    dst      TEXT NOT NULL,
    sport    INTEGER NOT NULL,
    dport    INTEGER NOT NULL,
    proto    INTEGER NOT NULL,
    bytes    INTEGER NOT NULL,
    packets  INTEGER NOT NULL,
    start    REAL NOT NULL,
    end      REAL NOT NULL,
    in_if    INTEGER NOT NULL,
    out_if   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flows_end ON flows(end);
CREATE INDEX IF NOT EXISTS idx_flows_link ON flows(exporter, out_if, end);
"""


class SqliteStore:
    def __init__(self, path: str = "flows.db"):
        # The collector thread writes while the CLI/API may read, so share one guarded connection.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            # WAL lets the collector process write while the web server process reads.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(SCHEMA)

    def add_flows(self, flows: Iterable[Flow]) -> None:
        rows = [
            (f.exporter, f.src, f.dst, f.sport, f.dport, f.proto, f.bytes, f.packets,
             f.start, f.end, f.in_if, f.out_if)
            for f in flows
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany("INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def top_talkers(self, since: float, limit: int = 10) -> list[tuple[str, int]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT src, SUM(bytes) b FROM flows WHERE end >= ? GROUP BY src ORDER BY b DESC LIMIT ?",
                (since, limit),
            )
            return [(r[0], r[1]) for r in cur.fetchall()]

    def top_flows(self, since: float, limit: int = 10) -> list[tuple[str, str, int, int]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT src, dst, dport, SUM(bytes) b FROM flows WHERE end >= ? "
                "GROUP BY src, dst, dport ORDER BY b DESC LIMIT ?",
                (since, limit),
            )
            return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]

    def link_bytes(self, exporter: str, interface: int, since: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(bytes), 0) FROM flows WHERE exporter = ? AND out_if = ? AND end >= ?",
                (exporter, interface, since),
            )
            return int(cur.fetchone()[0])

    def graph_edges(self, since: float, limit: int = 1000) -> list[tuple[str, str, int, int]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT src, dst, SUM(bytes) b, SUM(packets) FROM flows WHERE end >= ? "
                "GROUP BY src, dst ORDER BY b DESC LIMIT ?",
                (since, limit),
            )
            return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]

    def timeline(self, since: float, bucket_seconds: float) -> list[tuple[float, int]]:
        if bucket_seconds <= 0:
            raise ValueError("bucket_seconds must be positive")
        with self._lock:
            cur = self._conn.execute(
                "SELECT CAST(end / ? AS INTEGER) * ? AS t, SUM(bytes) FROM flows WHERE end >= ? "
                "GROUP BY t ORDER BY t",
                (bucket_seconds, bucket_seconds, since),
            )
            return [(float(r[0]), int(r[1])) for r in cur.fetchall()]

    def totals(self, since: float) -> tuple[int, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM flows WHERE end >= ?", (since,)
            )
            row = cur.fetchone()
            return int(row[0]), int(row[1])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

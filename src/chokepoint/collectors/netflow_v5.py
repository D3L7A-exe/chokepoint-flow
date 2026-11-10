"""NetFlow v5 parser and UDP collector.

Packet layout (all fields big-endian):
  header: 24 bytes, then `count` records of 48 bytes each.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from collections.abc import Callable, Iterable

from ..models import Flow

HEADER = struct.Struct("!HHIIIIBBH")  # 24 bytes
RECORD = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")  # 48 bytes
MAX_RECORDS = 30  # v5 exporters send at most 30 records per packet


class NetFlowV5Error(ValueError):
    """Raised for malformed or unsupported packets."""


def parse_packet(data: bytes, exporter: str) -> list[Flow]:
    """Parse one NetFlow v5 datagram into Flow objects."""
    if len(data) < HEADER.size:
        raise NetFlowV5Error("packet shorter than header")
    version, count, uptime_ms, secs, nsecs, _seq, _etype, _eid, _samp = HEADER.unpack_from(data)
    if version != 5:
        raise NetFlowV5Error(f"unsupported version {version}")
    if not 0 < count <= MAX_RECORDS:
        raise NetFlowV5Error(f"invalid record count {count}")
    expected = HEADER.size + count * RECORD.size
    if len(data) < expected:
        raise NetFlowV5Error("packet truncated")

    export_time = secs + nsecs / 1e9
    flows: list[Flow] = []
    for i in range(count):
        (src, dst, _nh, in_if, out_if, pkts, octets, first, last, sport, dport,
         _pad1, _flags, proto, _tos, _sas, _das, _smask, _dmask, _pad2) = RECORD.unpack_from(
            data, HEADER.size + i * RECORD.size
        )
        # first/last are router uptime in ms; convert to absolute time via export time.
        start = export_time - (uptime_ms - first) / 1000.0
        end = export_time - (uptime_ms - last) / 1000.0
        flows.append(
            Flow(
                exporter=exporter,
                src=str(ipaddress.IPv4Address(src)),
                dst=str(ipaddress.IPv4Address(dst)),
                sport=sport,
                dport=dport,
                proto=proto,
                bytes=octets,
                packets=pkts,
                start=start,
                end=max(end, start),
                in_if=in_if,
                out_if=out_if,
            )
        )
    return flows


def build_packet(flows: Iterable[Flow], uptime_ms: int = 3_600_000, export_time: float | None = None,
                 sequence: int = 0) -> bytes:
    """Build a NetFlow v5 datagram. Used by the simulator and by tests."""
    import time

    flows = list(flows)
    if not 0 < len(flows) <= MAX_RECORDS:
        raise ValueError(f"need 1..{MAX_RECORDS} flows per packet")
    t = time.time() if export_time is None else export_time
    secs, nsecs = int(t), int((t % 1) * 1e9)
    out = [HEADER.pack(5, len(flows), uptime_ms, secs, nsecs, sequence, 0, 0, 0)]
    for f in flows:
        first = max(0, uptime_ms - int((t - f.start) * 1000))
        last = max(0, uptime_ms - int((t - f.end) * 1000))
        out.append(
            RECORD.pack(
                ipaddress.IPv4Address(f.src).packed,
                ipaddress.IPv4Address(f.dst).packed,
                b"\x00\x00\x00\x00",
                f.in_if, f.out_if, f.packets, f.bytes, first, last,
                f.sport, f.dport, 0, 0, f.proto, 0, 0, 0, 0, 0, 0,
            )
        )
    return b"".join(out)


class UdpCollector:
    """Blocking UDP listener that hands parsed flows to a callback."""

    def __init__(self, host: str, port: int, on_flows: Callable[[list[Flow]], None]):
        self.on_flows = on_flows
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.settimeout(1.0)
        self._running = False
        self.dropped = 0

    @property
    def address(self) -> tuple[str, int]:
        return self.sock.getsockname()

    def serve_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                data, (src_ip, _port) = self.sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                flows = parse_packet(data, src_ip)
            except NetFlowV5Error:
                self.dropped += 1
                continue
            self.on_flows(flows)

    def stop(self) -> None:
        self._running = False
        self.sock.close()

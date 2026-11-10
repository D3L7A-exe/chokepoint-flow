import struct
import threading
import time

import pytest

from chokepoint.collectors import NetFlowV5Error, UdpCollector, build_packet, parse_packet
from chokepoint.collectors.netflow_v5 import HEADER, RECORD
from chokepoint.models import Flow


def make_flow(**kw):
    now = time.time()
    base = dict(exporter="", src="10.0.0.5", dst="8.8.8.8", sport=40000, dport=443, proto=6,
                bytes=123456, packets=100, start=now - 5, end=now - 1, in_if=1, out_if=2)
    base.update(kw)
    return Flow(**base)


def test_struct_sizes():
    assert HEADER.size == 24
    assert RECORD.size == 48


def test_roundtrip():
    t = 1_700_000_000.0
    original = make_flow(start=t - 5, end=t - 1)
    parsed = parse_packet(build_packet([original], export_time=t), "192.0.2.1")
    assert len(parsed) == 1
    f = parsed[0]
    assert (f.src, f.dst, f.sport, f.dport, f.proto) == ("10.0.0.5", "8.8.8.8", 40000, 443, 6)
    assert (f.bytes, f.packets, f.in_if, f.out_if) == (123456, 100, 1, 2)
    assert f.exporter == "192.0.2.1"
    assert f.start == pytest.approx(t - 5, abs=0.01)
    assert f.end == pytest.approx(t - 1, abs=0.01)


def test_multiple_records():
    flows = [make_flow(sport=1000 + i) for i in range(30)]
    assert [f.sport for f in parse_packet(build_packet(flows), "x")] == [1000 + i for i in range(30)]


@pytest.mark.parametrize("data", [b"", b"\x00" * 10])
def test_short_packet_rejected(data):
    with pytest.raises(NetFlowV5Error):
        parse_packet(data, "x")


def test_wrong_version_rejected():
    pkt = bytearray(build_packet([make_flow()]))
    struct.pack_into("!H", pkt, 0, 9)
    with pytest.raises(NetFlowV5Error):
        parse_packet(bytes(pkt), "x")


def test_truncated_rejected():
    pkt = build_packet([make_flow(), make_flow()])
    with pytest.raises(NetFlowV5Error):
        parse_packet(pkt[:-10], "x")


def test_bad_count_rejected():
    pkt = bytearray(build_packet([make_flow()]))
    struct.pack_into("!H", pkt, 2, 31)
    with pytest.raises(NetFlowV5Error):
        parse_packet(bytes(pkt), "x")


def test_build_rejects_too_many():
    with pytest.raises(ValueError):
        build_packet([make_flow()] * 31)


def test_udp_collector_end_to_end():
    import socket

    received = []
    done = threading.Event()

    def on_flows(flows):
        received.extend(flows)
        done.set()

    collector = UdpCollector("127.0.0.1", 0, on_flows)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b"garbage", collector.address)  # must be dropped, not crash
        sock.sendto(build_packet([make_flow()]), collector.address)
        assert done.wait(3)
        assert len(received) == 1
        assert received[0].exporter == "127.0.0.1"
        assert collector.dropped == 1
    finally:
        collector.stop()
        thread.join(timeout=3)

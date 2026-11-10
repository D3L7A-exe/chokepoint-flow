import pytest

from chokepoint.analysis import analyze_links, classify, load_links
from chokepoint.models import Flow, LinkConfig
from chokepoint.storage import SqliteStore

NOW = 1_700_000_000.0


def flow(src="10.0.0.1", dst="8.8.8.8", dport=443, bytes_=1000, end=NOW - 10, out_if=2, exporter="r1"):
    return Flow(exporter=exporter, src=src, dst=dst, sport=40000, dport=dport, proto=6,
                bytes=bytes_, packets=1, start=end - 1, end=end, in_if=1, out_if=out_if)


@pytest.fixture
def store():
    s = SqliteStore(":memory:")
    yield s
    s.close()


def test_top_talkers_ordered_and_windowed(store):
    store.add_flows([
        flow(src="a", bytes_=500), flow(src="b", bytes_=900), flow(src="a", bytes_=600),
        flow(src="old", bytes_=10**9, end=NOW - 10_000),  # outside the window
    ])
    assert store.top_talkers(NOW - 300) == [("a", 1100), ("b", 900)]


def test_top_flows(store):
    store.add_flows([flow(dport=443, bytes_=100), flow(dport=443, bytes_=100), flow(dport=22, bytes_=50)])
    assert store.top_flows(NOW - 300)[0] == ("10.0.0.1", "8.8.8.8", 443, 200)


def test_link_bytes_filters_exporter_and_interface(store):
    store.add_flows([
        flow(bytes_=100, out_if=2), flow(bytes_=200, out_if=3),
        flow(bytes_=400, exporter="r2", out_if=2),
    ])
    assert store.link_bytes("r1", 2, NOW - 300) == 100
    assert store.link_bytes("r1", 9, NOW - 300) == 0


@pytest.mark.parametrize("u,level", [(0.0, "ok"), (0.69, "ok"), (0.70, "warning"), (0.89, "warning"),
                                     (0.90, "critical"), (1.5, "critical")])
def test_classify(u, level):
    assert classify(u) == level


def test_analyze_links_flags_saturated_link(store):
    # 100 Mbit/s over 10 s on a 100 Mbit/s link is 125 MB total -> 100 % utilization
    store.add_flows([flow(bytes_=125_000_000, out_if=2), flow(bytes_=1_000_000, out_if=3)])
    links = [LinkConfig("uplink", "r1", 2, 100), LinkConfig("lan", "r1", 3, 1000)]
    result = analyze_links(store, links, window_seconds=10, now=NOW)
    assert [s.link.name for s in result] == ["uplink", "lan"]  # most loaded first
    assert result[0].mbps == pytest.approx(100.0)
    assert result[0].level == "critical"
    assert result[1].level == "ok"


def test_analyze_links_rejects_bad_window(store):
    with pytest.raises(ValueError):
        analyze_links(store, [], window_seconds=0)


def test_load_links(tmp_path):
    cfg = tmp_path / "links.toml"
    cfg.write_text('[[link]]\nname = "core"\nexporter = "10.0.0.1"\ninterface = 2\ncapacity_mbps = 1000\n')
    assert load_links(cfg) == [LinkConfig("core", "10.0.0.1", 2, 1000.0)]

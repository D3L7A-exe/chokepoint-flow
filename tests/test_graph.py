import pytest

from chokepoint.analysis import build_graph, classify_ip, load_names


@pytest.mark.parametrize("ip,kind", [
    ("192.168.1.5", "host"), ("10.10.10.1", "host"), ("100.75.184.20", "host"),
    ("8.8.8.8", "internet"), ("65.109.143.62", "internet"),
    ("239.255.255.250", "multicast"), ("255.255.255.255", "multicast"),
    ("not-an-ip", "host"),
])
def test_classify_ip(ip, kind):
    assert classify_ip(ip) == kind


EDGES = [
    ("192.168.1.115", "192.168.1.200", 1000, 10),
    ("192.168.1.200", "192.168.1.115", 400, 4),
    ("192.168.1.200", "8.8.8.8", 300, 3),
    ("192.168.1.115", "1.1.1.1", 200, 2),
    ("192.168.1.1", "239.255.255.250", 50, 1),
]


def test_collapse_external_groups_internet_and_multicast():
    g = build_graph(EDGES, 10, names={"192.168.1.200": "proxmox"})
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"192.168.1.115", "192.168.1.200", "192.168.1.1", "internet", "multicast"}
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    assert labels["192.168.1.200"] == "proxmox"
    assert labels["internet"] == "Internet"


def test_links_merge_both_directions():
    g = build_graph(EDGES, 10)
    link = next(l for l in g["links"] if {l["source"], l["target"]} == {"192.168.1.115", "192.168.1.200"})
    assert link["bytes"] == 1400
    assert sorted((link["tx"], link["rx"])) == [400, 1000]
    assert link["mbps"] == pytest.approx(1400 * 8 / 10 / 1e6)


def test_internet_traffic_from_several_hosts_aggregates():
    g = build_graph(EDGES, 10)
    inet = next(n for n in g["nodes"] if n["id"] == "internet")
    assert inet["bytes"] == 500
    assert g["links"][0]["bytes"] == 1400  # sorted busiest first


def test_without_collapse_external_hosts_stay_separate():
    g = build_graph(EDGES, 10, collapse_external=False)
    ids = {n["id"] for n in g["nodes"]}
    assert {"8.8.8.8", "1.1.1.1", "239.255.255.250"} <= ids
    assert "internet" not in ids


def test_self_links_are_dropped():
    g = build_graph([("8.8.8.8", "1.1.1.1", 100, 1)], 10)
    assert g["nodes"] == [] and g["links"] == []


def test_max_nodes_keeps_busiest_and_drops_orphan_links():
    edges = [(f"10.0.0.{i}", "10.0.1.1", 100 * i, 1) for i in range(1, 11)]
    g = build_graph(edges, 10, max_nodes=3)
    assert len(g["nodes"]) == 3
    kept = {n["id"] for n in g["nodes"]}
    assert all(l["source"] in kept and l["target"] in kept for l in g["links"])


def test_bad_window_rejected():
    with pytest.raises(ValueError):
        build_graph([], 0)


def test_load_names(tmp_path):
    f = tmp_path / "names.toml"
    f.write_text('[names]\n"192.168.1.200" = "proxmox"\n"192.168.1.127" = "chokepoint"\n')
    assert load_names(f) == {"192.168.1.200": "proxmox", "192.168.1.127": "chokepoint"}

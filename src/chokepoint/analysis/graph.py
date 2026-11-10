"""Turn host-to-host byte counts into a graph the UI can draw."""

from __future__ import annotations

import ipaddress
import tomllib
from pathlib import Path

INTERNET = "internet"
MULTICAST = "multicast"


def classify_ip(ip: str) -> str:
    """Return "multicast", "internet" (globally routable) or "host" (everything local)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "host"
    if addr.is_multicast or ip == "255.255.255.255":
        return MULTICAST
    if addr.is_global:
        return INTERNET
    return "host"


def load_names(path: str | Path) -> dict[str, str]:
    """Read a `[names]` table mapping IP address to a friendly name from TOML."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return {str(ip): str(name) for ip, name in data.get("names", {}).items()}


def build_graph(
    edges: list[tuple[str, str, int, int]],
    window_seconds: float,
    names: dict[str, str] | None = None,
    collapse_external: bool = True,
    max_nodes: int = 60,
) -> dict:
    """Aggregate directed (src, dst, bytes, packets) rows into nodes and links.

    With `collapse_external`, all public addresses become one "Internet" node and all
    multicast/broadcast traffic one "Multicast" node, which keeps the map readable.
    Links are undirected pairs; `tx` is bytes from `source` to `target`, `rx` the reverse.
    """
    names = names or {}
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")

    def node_for(ip: str) -> tuple[str, str, str]:
        kind = classify_ip(ip)
        if collapse_external and kind == INTERNET:
            return INTERNET, "Internet", INTERNET
        if collapse_external and kind == MULTICAST:
            return MULTICAST, "Multicast", MULTICAST
        return ip, names.get(ip, ip), kind

    nodes: dict[str, dict] = {}
    pairs: dict[tuple[str, str], dict] = {}
    for src, dst, nbytes, packets in edges:
        a, a_label, a_kind = node_for(src)
        b, b_label, b_kind = node_for(dst)
        if a == b:
            continue
        for nid, label, kind in ((a, a_label, a_kind), (b, b_label, b_kind)):
            n = nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, "bytes": 0})
            n["bytes"] += nbytes
        source, target = sorted((a, b))
        link = pairs.setdefault((source, target), {"source": source, "target": target, "tx": 0, "rx": 0, "packets": 0})
        link["tx" if a == source else "rx"] += nbytes
        link["packets"] += packets

    # Keep the busiest nodes only, and drop links that lost an endpoint.
    keep = {n["id"] for n in sorted(nodes.values(), key=lambda n: n["bytes"], reverse=True)[:max_nodes]}
    node_list = sorted((n for n in nodes.values() if n["id"] in keep), key=lambda n: n["bytes"], reverse=True)
    link_list = []
    for link in pairs.values():
        if link["source"] in keep and link["target"] in keep:
            total = link["tx"] + link["rx"]
            link["bytes"] = total
            link["mbps"] = total * 8 / window_seconds / 1e6
            link_list.append(link)
    link_list.sort(key=lambda link: link["bytes"], reverse=True)
    return {"window_seconds": window_seconds, "nodes": node_list, "links": link_list}

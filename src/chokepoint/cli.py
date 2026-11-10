"""Command line interface: collect, simulate, top, bottlenecks."""

from __future__ import annotations

import argparse
import ipaddress
import os
import random
import socket
import sys
import time

from . import __version__
from .analysis import analyze_links, load_links, load_names
from .collectors import UdpCollector, build_packet
from .models import Flow
from .server import Dashboard, make_server
from .storage import SqliteStore


def _hostport(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT")
    return host, int(port)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def cmd_collect(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    host, port = args.listen
    collector = UdpCollector(host, port, store.add_flows)
    print(f"Listening for NetFlow v5 on {host}:{port}, writing to {args.db} (Ctrl+C to stop)")
    try:
        collector.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        store.close()
        print(f"Stopped. Malformed packets dropped: {collector.dropped}")
    return 0


def _simulated_flows(exporter_if: int, n: int, rng: random.Random) -> list[Flow]:
    hosts = [f"192.168.1.{i}" for i in (10, 20, 30, 40, 50)]
    servers = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "142.250.74.14"]
    now = time.time()
    flows = []
    for _ in range(n):
        heavy = rng.random() < 0.15  # a few heavy talkers create a visible bottleneck
        size = rng.randint(5_000_000, 40_000_000) if heavy else rng.randint(500, 200_000)
        flows.append(
            Flow(
                exporter="", src=rng.choice(hosts), dst=rng.choice(servers),
                sport=rng.randint(30000, 60000), dport=rng.choice([443, 443, 80, 53, 22]),
                proto=6, bytes=size, packets=max(1, size // 1200),
                start=now - rng.uniform(1, 20), end=now, in_if=1, out_if=exporter_if,
            )
        )
    return flows


def cmd_simulate(args: argparse.Namespace) -> int:
    host, port = args.target
    rng = random.Random(args.seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    remaining, seq = args.count, 0
    while remaining > 0:
        batch = min(remaining, 30)
        sock.sendto(build_packet(_simulated_flows(args.out_if, batch, rng), sequence=seq), (host, port))
        remaining -= batch
        seq += batch
        time.sleep(0.01)
    print(f"Sent {args.count} simulated flows to {host}:{port}")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    since = time.time() - args.window
    print(f"Top talkers (last {args.window:.0f}s)")
    for src, b in store.top_talkers(since, args.limit):
        print(f"  {src:<18}{_human(b):>12}")
    print("\nTop flows")
    for src, dst, dport, b in store.top_flows(since, args.limit):
        print(f"  {src:<16} -> {dst:<16}:{dport:<6}{_human(b):>12}")
    store.close()
    return 0


def cmd_bottlenecks(args: argparse.Namespace) -> int:
    store = SqliteStore(args.db)
    links = load_links(args.config)
    statuses = analyze_links(store, links, window_seconds=args.window)
    marks = {"ok": "OK      ", "warning": "WARNING ", "critical": "CRITICAL"}
    print(f"Link utilization (last {args.window:.0f}s)")
    for s in statuses:
        print(f"  {marks[s.level]} {s.link.name:<20}{s.mbps:>9.2f} Mbit/s of "
              f"{s.link.capacity_mbps:.0f} ({s.utilization * 100:.1f}%)")
    store.close()
    return 1 if any(s.level == "critical" for s in statuses) else 0


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def cmd_serve(args: argparse.Namespace) -> int:
    host, port = args.listen
    auth = args.auth or os.environ.get("CHOKEPOINT_AUTH") or None
    if auth and ":" not in auth:
        print("error: auth must look like user:password", file=sys.stderr)
        return 2
    links = load_links(args.config) if args.config and os.path.exists(args.config) else []
    names = load_names(args.names) if args.names and os.path.exists(args.names) else {}
    store = SqliteStore(args.db)
    server = make_server(host, port, Dashboard(store, links, names, auth))
    print(f"Chokepoint web UI on http://{host}:{port}/  (db: {args.db}, Ctrl+C to stop)")
    if not _is_loopback(host) and not auth:
        print("warning: listening on a non-loopback address WITHOUT authentication; "
              "set CHOKEPOINT_AUTH=user:password or use --auth", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chokepoint", description="Network flow visualization and bottleneck detection")
    p.add_argument("--version", action="version", version=f"chokepoint {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="receive NetFlow v5 and store it")
    c.add_argument("--listen", type=_hostport, default=("0.0.0.0", 2055), metavar="HOST:PORT")
    c.add_argument("--db", default="flows.db")
    c.set_defaults(func=cmd_collect)

    s = sub.add_parser("simulate", help="send simulated NetFlow v5 traffic")
    s.add_argument("--target", type=_hostport, default=("127.0.0.1", 2055), metavar="HOST:PORT")
    s.add_argument("--count", type=int, default=200)
    s.add_argument("--out-if", type=int, default=2, help="output interface index to simulate")
    s.add_argument("--seed", type=int, default=None)
    s.set_defaults(func=cmd_simulate)

    t = sub.add_parser("top", help="show top talkers and flows")
    t.add_argument("--db", default="flows.db")
    t.add_argument("--window", type=float, default=300)
    t.add_argument("--limit", type=int, default=10)
    t.set_defaults(func=cmd_top)

    b = sub.add_parser("bottlenecks", help="show link utilization and flag bottlenecks")
    b.add_argument("--db", default="flows.db")
    b.add_argument("--config", default="links.toml")
    b.add_argument("--window", type=float, default=300)
    b.set_defaults(func=cmd_bottlenecks)

    w = sub.add_parser("serve", help="run the web UI and JSON API")
    w.add_argument("--listen", type=_hostport, default=("127.0.0.1", 8080), metavar="HOST:PORT")
    w.add_argument("--db", default="flows.db")
    w.add_argument("--config", default="links.toml", help="link capacities (optional)")
    w.add_argument("--names", default="names.toml", help="IP to name mapping (optional)")
    w.add_argument("--auth", default=None, metavar="USER:PASSWORD",
                   help="enable HTTP Basic auth (prefer the CHOKEPOINT_AUTH environment variable)")
    w.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

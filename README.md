# Chokepoint

Network flow visualization with automatic bottleneck detection.

Chokepoint collects flow data from your network (NetFlow today, IPFIX/sFlow/SNMP planned),
stores it, and shows where traffic is heavy and where links are close to saturation.
It is built to run in a homelab and to scale to larger environments by swapping
the storage backend and adding collectors.

> Status: early development (v0.1). The collector, storage and bottleneck analysis
> work from the command line; the web UI is next. See [docs/roadmap.md](docs/roadmap.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Terminal 1: receive NetFlow v5 on UDP 2055 and store it in SQLite
chokepoint collect --listen 0.0.0.0:2055 --db flows.db

# Terminal 2: send simulated traffic (no real router needed)
chokepoint simulate --target 127.0.0.1:2055 --count 200

# Show top talkers and link utilization
chokepoint top --db flows.db
chokepoint bottlenecks --db flows.db --config links.toml
```

`links.toml` describes link capacity so utilization can be computed:

```toml
[[link]]
name = "core-uplink"
exporter = "10.0.0.1"
interface = 2
capacity_mbps = 1000
```

## Web UI

```bash
chokepoint serve --db chokepoint.db --config links.toml --names names.toml
# open http://127.0.0.1:8080
```

- `links.toml`: link capacities for bottleneck verdicts (see `links.example.toml`).
- `names.toml`: friendly names, e.g. `[names]` table mapping `"192.168.1.10" = "proxmox"`.
- Public IPs are grouped into "Internet" and multicast into "Multicast" (toggle in the UI).

The server listens on `127.0.0.1` by default. To expose it on your LAN, enable authentication:

```bash
export CHOKEPOINT_AUTH='admin:a-long-random-password'
chokepoint serve --listen 0.0.0.0:8080
```

HTTP Basic auth sends credentials in clear text. Put it behind a reverse proxy with TLS if it
leaves a trusted network. The UI has no external dependencies and uses a strict CSP.

## How it works

```
Routers / switches / softflowd  ->  Collector  ->  Storage  ->  Analysis  ->  CLI / API / UI
        (NetFlow v5)             (UDP listener)    (SQLite)    (bottlenecks)
```

See [docs/architecture.md](docs/architecture.md) for the design.

## Sending real flows

Any exporter that speaks NetFlow v5 works. On a Linux host or Proxmox bridge, `softflowd`
is the quickest way to start:

```bash
softflowd -i vmbr0 -n <collector-ip>:2055 -v 5
```

## Development

```bash
pytest
```

## License

MIT

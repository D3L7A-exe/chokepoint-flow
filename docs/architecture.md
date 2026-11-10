# Architecture

Chokepoint is a pipeline of small, replaceable parts.

## Components

1. **Collectors** (`chokepoint.collectors`): one module per source. Each turns raw
   packets into normalized `Flow` objects. Implemented: NetFlow v5.
   Planned: NetFlow v9/IPFIX, sFlow, SNMP interface counters.
2. **Storage** (`chokepoint.storage`): a small interface (`FlowStore`) with a SQLite
   backend. Larger deployments get a ClickHouse or TimescaleDB backend behind the
   same interface, so the rest of the code does not change.
3. **Analysis** (`chokepoint.analysis`): turns stored flows into answers:
   top talkers, per-link throughput, utilization against configured capacity,
   and a bottleneck verdict per link.
4. **Interfaces**: CLI today. REST/WebSocket API and a web UI (topology map,
   Sankey diagram, bottleneck view) are next.

## Data model

A `Flow` is one unidirectional conversation record: source/destination address and
port, protocol, bytes, packets, input/output interface, the exporting device, and
the time window it covers. Everything downstream works on this one shape, which is
what makes new collectors cheap to add.

## Bottleneck detection

Version 0.1 uses utilization: bytes seen on a link over a window, divided by the
link's configured capacity. Links above the warning (default 70 %) and critical
(default 90 %) thresholds are flagged. Capacity comes from a config file for now
and from SNMP (`ifSpeed`) later.

Planned signals: TCP retransmissions and RTT (via Zeek/Suricata or active probes),
and baseline-versus-now deviation for anomaly detection.

## Scaling notes

- The collector is a single UDP listener, which is fine for homelab and small to
  medium networks. For very high flow rates the collector can be rewritten in
  Go or Rust behind the same storage interface.
- Aggregate on write (per minute) and keep raw flows only for a short window.

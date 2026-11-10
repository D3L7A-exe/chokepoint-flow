# Roadmap

## v0.1 (current): core pipeline
- [x] NetFlow v5 parser and UDP collector
- [x] SQLite storage
- [x] Top talkers and link utilization
- [x] Bottleneck verdicts from configured capacity
- [x] CLI and traffic simulator

## v0.2: see it
- [x] JSON API and live-refreshing dashboard (stdlib server, no dependencies)
- [x] Web UI: network map with named hosts, link thickness and color by traffic
- [x] Bottleneck panel, top talkers, traffic over time, accessible table view
- [x] Optional HTTP Basic auth
- [ ] Sankey view of top flows
- [x] Time windows (5 min to 24 h)

## v0.3: more sources
- [ ] NetFlow v9 / IPFIX
- [ ] SNMP: interface speed, counters, errors
- [ ] LLDP/CDP topology discovery
- [ ] Alerts (webhook, Discord, email)

## v1.0: production
- [ ] ClickHouse / TimescaleDB backend
- [ ] Authentication and roles, multi-site
- [ ] Docker Compose deployment
- [ ] Baseline and anomaly detection, Wazuh integration
- [ ] CI, docs and release process

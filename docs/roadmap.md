# Roadmap

## v0.1 (current): core pipeline
- [x] NetFlow v5 parser and UDP collector
- [x] SQLite storage
- [x] Top talkers and link utilization
- [x] Bottleneck verdicts from configured capacity
- [x] CLI and traffic simulator

## v0.2: see it
- [ ] REST API (FastAPI) and WebSocket live updates
- [ ] Web UI: network map with link thickness and color by load
- [ ] Sankey view of top flows
- [ ] Time slider and history

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

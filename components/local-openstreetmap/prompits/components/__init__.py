"""Install, update, run and watch local components of a multi-agent system.

A *component* is a separately packaged program (an agent, a data source, a local service) that runs on the
user's computer. This package is the generic engine behind a component manager:

- `parts_list`: the signed list of components a publisher offers, with per-platform downloads and checksums.
- `installer`: download, verify and unpack a component beside its older versions; switch, roll back, remove.
- `state`: the manager's own small records (installed versions, running processes, events), kept on disk.
- `supervisor`: start and stop components, adopt ones still running after the manager restarts, restart
  crashed ones with growing delays, and rotate their logs.
- `health`: the one request every component answers about itself, and the manager's probe for it.
- `manager`: `ComponentManager`, one facade over all of the above that returns plain JSON-ready dicts.

It names no product, publisher or component; those come from the parts list and the caller.
"""

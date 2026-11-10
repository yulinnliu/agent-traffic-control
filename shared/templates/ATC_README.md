# Agent Traffic Control

Sync writes `README.md`, `atc.lock` and `governance/` in this directory;
nothing edits them by hand. `tasks/` holds this repository's own task records.

Governance bootloader:
1. Use an exact governance source/revision explicitly supplied by task authority, read from that exact Git revision.
2. Otherwise this repository's governance is the copy under `.atc/governance/`; `.atc/atc.lock` names its source and exact governance revision.
3. If neither can be resolved authoritatively, stop.

Read `.atc/governance/INDEX.md` and `CORE.md` first, then only the modules `INDEX.md` routes to for the task. Project/repository rules in `AGENTS.md` may be stricter and remain authoritative for this project.

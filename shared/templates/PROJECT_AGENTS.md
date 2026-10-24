<!-- atc:project-policy-reference begin -->
Agent Traffic Control governance bootloader:
1. Use an exact governance source/revision explicitly supplied by task authority, read from that exact Git revision.
2. Otherwise this repository's pinned governance is the copy under `.atc/governance/`; `.atc/governance.yaml` names its source and exact governance revision. Sync writes both; nothing edits them by hand.
3. If neither can be resolved authoritatively, stop.

Read `.atc/governance/INDEX.md` and `CORE.md` first, then only the modules `INDEX.md` routes to for the task. Project/repository rules in this `AGENTS.md` may be stricter and remain authoritative for this project.
<!-- atc:project-policy-reference end -->

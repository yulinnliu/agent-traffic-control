# Playbook lifecycle and task archive

**Decision.** No executable playbook format, one-shot consumption tracking or
task archive here.

**Why.** A playbook is a task description plus a dispatch contract. The task
description belongs to the project it changes; the dispatch contract belongs
to whatever orchestrates. Tracking whether a one-shot playbook was consumed
required durable admission state, a guard on every creation path and
historical reconciliation, which is orchestration state by another name.

**Evidence.** `hami-opc/workflows/_schema.md`,
`docs/specs/PLAYBOOK_LIFECYCLE_AND_TASK_ARCHIVE_DESIGN.md`, and the
2026-09-11 direction correction that staged its delivery.

**Instead.** A project keeps its task specs in its own docs. The only part
retained is the verification descriptor principle in `VERIFICATION.md`: a
verify step is an executable and argv, never a shell string.

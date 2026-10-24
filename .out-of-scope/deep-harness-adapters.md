# Deep harness adapters

**Decision.** A harness adapter is a thin file that points at the canonical
source and supplies that tool's constants. No profile packages, patch files,
plugin code or vendored upstream examples for any one harness.

**Why.** A developer-preview harness ships breaking changes; every line
written against its schema is rewritten later. The test from the
predecessor's constitution stands: if that harness disappeared tomorrow, the
abstraction must still make sense.

**Evidence.** `hami-opc/profiles/`, `cordis/` and the generated `dsh/policy.yml`
snapshot that had to be kept equal to hand-written adapter rows because the
harness could not load it. `docs/dev/DSH_INTEGRATION_NOTES.md` lists the gaps.

**Instead.** `.claude/`, `.agents/` and `.dsh/` hold adapters of a few lines.
Enforcement configs are generated from `shared/policies/` into each harness's
native format and committed in the consuming project, where CI diffs them.

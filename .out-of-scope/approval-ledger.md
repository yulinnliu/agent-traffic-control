# Hash-chained approval ledger

**Decision.** Approvals are recorded in the delivery record and the
repository's own history, not in a separate append-only, hash-chained ledger
with its own writer tool.

**Why.** The ledger needed a privileged writer, a policy envelope per
project, TTLs and a refusal path, all to answer a question git already
answers: which SHA did the owner approve, and when. For one owner the extra
machinery added a second trust root without adding assurance.

**Evidence.** `hami-opc/policies/test-approval-policy.yaml`,
`docs/specs/PLAYBOOK_TEST_APPROVAL_DESIGN.md`, and the
`scripts/lib/test-approval-ledger.mjs` implementation with its tests.

**Instead.** `DELIVERY.md` records authority used per delivery; an approval
is a commit, a tag or a signed statement naming the SHA.

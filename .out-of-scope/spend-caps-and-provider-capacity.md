# Spend caps and provider capacity

**Decision.** No budget ledger, per-call reservation, retry budget or
cross-provider fallback policy here.

**Why.** These are economics of a particular paid provider and harness, not
rules about who may write or how work changes hands. They change with every
pricing page and belong to the harness adapter or the owner's billing setup.

**Evidence.** `hami-opc/policies/spend-caps.yaml` and `capacity.yaml`, with
the atomic reservation and settlement code in `scripts/lib/`. Nothing in them
survived the removal of that provider.

**Instead.** A harness that meters spend enforces its own cap; a project that
needs a budget rule writes it in its own repository.

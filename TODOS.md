# TODOS

- [ ] **E9 — Agentic tool-loop fallback for zero-observation cases** (P3). What: an LLM loop with grep/sqlite/read tools that proposes steps when no parser or attribution produced anything. Why: last-resort coverage. Pros: coverage. Cons: nondeterministic under an exact-match grader. Context: see code/docs/PLAN.md Phase 1 E9; only after samples pass. Effort M (human) / S (CC). Depends on: E3 unparsed-lines report.
- [ ] **Evidence connectors + continuous re-audit** (P3). What: pluggable connectors replacing the static dataset/ loader. Why: the 12-month ideal in PLAN.md 0C. Effort XL/L. Depends on: engine stable.

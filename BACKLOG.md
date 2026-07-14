# Backlog — `feature/ui-messaging`

Tracks deferred work on this branch. Each item lists where the stub
lives, what "done" looks like, and any design context. When you start
an item, move it to `In progress`. When you finish, remove it (the diff
is the record).

Items are scoped to this branch. Anything broader belongs in a GitHub
issue.

---

## Per-agent traffic metering — perf & follow-ups

Phase A (commit `ba2f3a3`) classifies chat-shaped ledger rows with
`request_kind`. Phase B1 (`5072f33`) added always-on `(agent_id, path_kind)`
counters and the operator-controlled mode CRUD. Phase B2 (`048ab7f`)
wired the mode into proxy ledger writes — non-chat traffic now lands
real ledger rows when the operator flips a tuple to `full_logging`.

Open follow-ups on this layer:

- **Proxy hot-path perf**: counter UPSERT on every request + mode cache
  lookup adds DB load proportional to traffic. Untested at scale.
  **Do**: run a load test with realistic chat:embedding ratios (e.g.,
  1:10) before flipping any production agent's embedding path to
  `full_logging`. If contention shows up, batch the UPSERT in-process
  with a periodic flush — but that means proxy state the Rust port has
  to replicate, so prefer raising connection pool sizing first.
- **End-to-end test for B2** is deferred — would need `respx` for httpx
  mocking, which isn't a project dep. `_should_log_for_agent` and
  `_log_non_chat_entry` are both unit-tested directly. Add coverage if
  the gate misbehaves in real traffic.
- **Cross-process cache invalidation**: B2's 5s TTL handles multi-proxy
  deployments via eventual consistency. If we ever need <5s
  responsiveness (e.g., for security-driven flips), wire a pub/sub
  channel via Postgres `LISTEN/NOTIFY` so all proxies invalidate on
  flip immediately.
- **Non-200 logging for B2 rows**: today only `response.status_code ==
  200` lands a ledger row. Operators may want to see "embeddings have
  been 500-ing for 24h" — extend the gate to log error rows under
  `full_logging` mode if that need surfaces.

Design context: project memory `project_per_agent_traffic_metering.md`.

---

## V1.1 wiring — stubs that still pop a toast

### Add to Policy
- **Stub:** `frontend/src/pages/agent-chains.tsx:453` — admin action button
  emits `toast.info("Policy editor coming in next release")`.
- **Done when:** clicking the button opens a sheet pre-filled with the
  chain's offending finding (rule kind, scope = this agent), and
  submitting persists a row that the proxy enforces on the next request.
- **Backend:** reuse `dlp_rules` with a `kind='policy'` variant and a
  per-agent scope; expose as `POST /api/policy-rules` (or fold into
  `POST /api/dlp-rules` with the new `kind`).
- **Design context:** plan file `cuddly-dazzling-peach.md` — "Item 11".

### Acknowledge chain
- **Stub:** `frontend/src/pages/agent-chains.tsx:452` — `Acknowledge`
  button emits `toast.success("Chain acknowledged")` with no backend
  persistence.
- **Done when:** acknowledgement is persisted (who/when/which chain) and
  the chain row shows the ack state on next load. Threats & Alerts has
  the same need for individual alerts — share the table if shapes match.
- **Note:** not in the original V1.1 plan; surfaced while auditing the
  remaining `toast.info` stubs.

---

## Verify-before-claiming

Items the plan listed under Tier 3 polish but appear implemented during
a spot-check. Each needs a 5-minute manual pass before we cross it off
for good; if anything's half-wired it goes back into the V1.1 list.

- **Fleet Status:** baseline corridor + anomaly markers + severity chips
  (code at `pages/fleet-status.tsx:93–144`). Verify the corridor renders
  on the activity chart and chips actually filter the feed.
- **Network Map:** unknowns counter + flow-click side panel. Verify
  click-through opens the side panel with non-empty agent/session lists.
- **Sessions:** classification tags + collapsed long messages
  (`pages/sessions.tsx:72,99`). Verify long messages collapse with an
  "expand" affordance.
- **Audit Log:** row-hover preview (`pages/audit-log.tsx:376–453`).
  Verify the ~400ms hover delay still feels intentional, not laggy.
- **Agent Activity:** Tokens/Calls toggle + Agent Detail
  (`pages/agent-activity.tsx:327,388`). Verify both metrics swap
  cleanly and the detail block doesn't double-fetch.
- **PDF export endpoints:** `/api/export/{incident-report,compliance-report,compliance-evidence,audit-log}`
  exist. Run `scripts/verify_pdf.py` (if present) against a real export
  to confirm the embedded signature verifies against the stored pubkey.
- **Agent blocks:** `agent_blocks` table + `policy_block` enforcement in
  `server.py:500–507`. Verify a blocked agent receives 403 and the
  block surfaces in Audit Log as `action_type='policy_block'`.
- **Intent classifier:** `intent_classifier` module wired into
  `/api/sessions`. Verify a freshly-settled session gets a non-keyword
  intent within ~5 min and the result appears in the UI.

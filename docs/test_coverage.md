# Test Coverage — Summary

**Date:** 2026-07-14 (numbers from a full local run of both suites on this date)
**Question:** What does the test suite cover today, and where are the gaps?

## TL;DR

| Layer | Tests | Coverage | Notes |
|---|---|---|---|
| **Backend** (`src/kyde`, pytest) | 943 passing, 6 skipped | **82%** lines (1134 / 6408 missed) | 55 test files |
| **Frontend** (`frontend/src`, Vitest) | 164 passing | **42%** statements / **55%** branch | 52 test files |

The security- and integrity-critical backend paths (DLP, triage, auth,
settings, notifications, the proxy data plane) are at or near full coverage.
The frontend covers all pure logic and every route component's render states;
the remaining gap is deeper interaction coverage inside the large forensic
pages.

The 6 skipped backend tests exercise `kyde.signing`, which lives in the
private `kyde-enterprise` package since the edition split — they skip
gracefully in this public tree and run in the `gateway-enterprise` pipeline.

---

## How to run

```bash
# Backend (needs the kyde-postgres container on 127.0.0.1:5432)
TEST_POSTGRES_URL="postgresql://kyde:kyde-dev-only@localhost:5432" \
  uv run --extra test \
  python -m pytest tests/ --cov=src --cov-report=term-missing

# Frontend
cd frontend && npm run coverage
```

Note: `TEST_POSTGRES_URL` is the base URL **without** a database suffix;
conftest appends the test-database name and creates that DB on first run.

Caveat: the frontend interaction tests use a 5 s timeout — run them on an
otherwise idle machine.

CI runs both suites with coverage on every push/PR and, on pushes to `main`,
publishes the percentages as the README's coverage badges (see
`.github/workflows/ci.yml`, `badges` job).

---

## Backend — strong on the paths that matter

Core security / integrity paths:

| Module | Coverage |
|---|---|
| `auth.py`, `settings.py`, `smtp_sender.py`, `notifications.py`, `dlp_triage.py`, `mcp_policy.py` | 100% |
| `dlp.py` | 99% |
| `host_resolver.py`, `topology.py`, `mcp_registry.py` | 97–98% |
| `trust.py` | 96% |
| `audit_log.py`, `mcp_aggregator.py` | 95% |
| `pdf_export.py` | 94% |
| `network_origin.py` | 93% |
| `ledger.py` | 92% |
| `dlp_json_walk.py` | 91% |
| `crypto.py` | 90% |
| `server.py` (proxy data plane: routing, request handler, streaming) | 89% |
| `mcp_proxy.py` | 88% |

Remaining gaps, in rough priority order:

| Module | Coverage | Assessment |
|---|---|---|
| `dashboard.py` | 76% | Control-plane API. The uncovered share is export-endpoint error branches, scattered edge branches, and HTML template constants — the endpoint logic and RBAC gates are tested. |
| `telemetry.py` | 75% | Metrics emission glue. |
| `mcp_ledger.py` | 79% | Error/fallback branches of the MCP ledger writer. |
| `dlp_policies.py` | 82% | Policy CRUD edge branches. |
| `intent_classifier.py` | 86% | Heuristic fallback branches. |
| `config.py` / `migrations` / `testing.py` | 68–70% | Config + schema setup glue. |

Low-value, intentionally untested: `commands.py` (10%, CLI entrypoints),
`proxy.py` (4-line shim).

`signing.py` is no longer part of this tree — the byte-level signing contract
and its tests moved to the private `kyde-enterprise` package with the edition
split, and are exercised by that repo's CI.

## Frontend — logic covered, deep page interactions not

By area (statements):

| Area | Coverage | Notes |
|---|---|---|
| `lib/` | 100% | `format`, `host-format`, `serial-ids`, `agent-names`, `request-kind`, `session-names`, `utils` |
| `pages/settings/` | 66% | settings subpages |
| `pages/` | 51% | every route component has a test covering render/loading/empty/error states plus key interactions (`dlp-rules` add + validate, `users` roster + RBAC, `profile` password change) |
| `components/ui` | 52% | shadcn primitives, covered incidentally |
| `hooks/` | 36% | `use-me` 100%; `use-prefetch` and scroll hooks untested |
| `components/shared` | 21% | tested: `status-badge`, `trust-score`, `relative-time`, `require-admin`, `mcp-server-dialog`, `users-dialog`, `date-range-picker`; the large detail-dialog/sheet components are not |
| `api/` | 21% | `client.ts` covered; generated query helpers are not |
| `components/layout` | 2% | app shell — exercised indirectly by page tests, only `require-admin` directly |

The heaviest forensic pages (`sessions`, `threats-alerts`, `audit-log`,
`timeline`, `policies`, `compliance`, `fleet-status`, `agent-detail`,
`host-detail`, `agent-chains`, `agent-activity`) have mount/loading smoke
tests; their deeper interactions (filtering, drill-downs, bulk actions) are
the main untested surface.

Reusable patterns established for page tests: hoisted mock holders for
per-test hook returns, `importOriginal` spread to keep module constants while
overriding hooks, child-component stubbing, and an explicit recharts stub (a
Proxy-everything mock answers `then` and hangs the import).

---

## Priorities

1. **Frontend forensic-page interactions** — the largest remaining gap:
   filtering/drill-down/bulk-action flows inside the big pages, plus the
   untested shared detail dialogs/sheets.
2. **`dashboard.py` export error branches** — low risk, but the export
   endpoints are compliance-facing.
3. **Layout components** (`app-shell`, `sidebar`, `notifications-bell`) —
   cheap smoke tests would close the most visible 0% rows.

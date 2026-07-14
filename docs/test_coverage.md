# Test Coverage — Summary

**Date:** 2026-06-22
**Commit:** test-coverage expansion (Vitest frontend suite + backend unit tests)
**Question:** What does the test suite cover today, and where are the gaps?

## TL;DR

| Layer | Tests | Coverage | Notes |
|---|---|---|---|
| **Backend** (`src/witness`, pytest) | 675 passing | **72%** lines (1781 / 6324 missed) | 49 test files |
| **Frontend** (`frontend/src`, Vitest) | 164 passing | **42%** statements / **55%** branch (pages now 51%) | 52 test files |

Unit-level coverage of pure functions and data transforms is strong on both
sides. The untested surface is concentrated in (1) a few high-stakes backend
modules — DLP and signing especially — and (2) essentially all React
page/view components.

> **Update (post-2026-06-22):** the high-stakes backend gaps are now closed —
> `dlp.py` 48%→99%, `dlp_triage.py` 29%→100%, `signing.py` 51%→59% (TPM
> hardware path is the rest), `server.py` 65%→90%, `auth.py` 33%→100%,
> `settings.py` 62%→100%, `smtp_sender.py` 28%→100%, `notifications.py`
> 16%→100%, `dashboard.py` 68%→81%. Added `test_dlp_scanners.py` (68),
> `test_dlp_triage.py`, `test_signing_keys.py`, `test_proxy_helpers.py` (45),
> `test_proxy_handler.py` (17), `test_auth.py` (24), `test_settings.py` (37),
> `test_smtp_sender.py` (24), `test_notifications.py` (25),
> `test_dashboard_endpoints.py` (46). The 72% backend aggregate below predates
> these additions and is now substantially higher. Remaining backend gaps are
> lower-stakes: `dashboard.py`'s export-endpoint error branches + HTML
> templates, and `config.py`/`migrations` glue.
>
> **Frontend (priority #4) — done:** `pages/` went from ~0% to **51%
> statements** (settings subpages 66%; whole frontend 8.8%→42%). **Every**
> route component now has a test (32 page files, 164 frontend tests total)
> covering render/loading/empty/error states plus key interactions
> (`dlp-rules` add+validate, `users` roster+RBAC, `profile` password change).
> The heaviest forensic pages (`sessions`, `threats-alerts`, `audit-log`,
> `timeline`, `policies`, `compliance`, `fleet-status`, `agent-detail`,
> `host-detail`, `agent-chains`, `agent-activity`) have mount/loading smoke
> tests. Reusable patterns established: hoisted mock holders for per-test hook
> returns, `importOriginal` spread to keep module constants while overriding
> hooks, child-component stubbing, and an explicit recharts stub (a
> Proxy-everything mock answers `then` and hangs the import). The remaining
> frontend gap is deeper interaction coverage inside the large forensic pages,
> not whole-page absence.

The headline frontend number (8.8%) is misleading: it is dragged down by
route-level UI components at 0%. The logic-bearing code is near-complete.

---

## How to run

```bash
# Backend (needs the kyde-postgres container on 127.0.0.1:5432)
TEST_POSTGRES_URL="postgresql://kyde:kyde-dev-only@localhost:5432" \
  uv run --extra test --with pytest-cov \
  python -m pytest tests/ --cov=src --cov-report=term-missing

# Frontend
cd frontend && npm run coverage
```

Note: `TEST_POSTGRES_URL` is the base URL **without** a database suffix;
conftest appends `/witness_test` and creates that DB on first run.

---

## Backend — solid, with real soft spots

Core security / integrity paths are well covered:

| Module | Coverage |
|---|---|
| `mcp_policy.py` | 100% |
| `trust.py`, `topology.py`, `pdf_export.py`, `host_resolver.py` | 98% |
| `enforce/blocklist.py`, `mcp_registry.py` | 97% |
| `audit_log.py`, `mcp_aggregator.py` | 95% |
| `network_origin.py` | 93% |
| `mcp_ledger.py`, `dlp_json_walk.py` | 91–92% |
| `crypto.py`, `ledger.py`, `enforce/prevention.py` | 89–90% |

Gaps that matter (not just CLI/glue):

| Module | Coverage | Why it matters |
|---|---|---|
| `dlp.py` | 🟢 99% | core DLP detection logic — **was 48%**; scanner HTTP paths, allowlist filter, store loop, retrospective sweep now covered (`test_dlp_scanners.py`) |
| `dlp_triage.py` | 🟢 100% | triage state machine — **was 29%** (`test_dlp_triage.py`) |
| `signing.py` | 🟡 59% | byte-level signing contract — **frozen across the Python→Rust split**. **was 51%**; key mgmt + Ed25519/ECDSA verify + TPM dispatch covered (`test_signing_keys.py`). Remaining 41% is the `TpmSigner` hardware path (`ESAPI()` / `tpm2_pytss`) — needs a TPM (or simulator), not unit-testable |
| `server.py` | 🟢 90% | the proxy data plane — **was 65%**; routing/normalization helpers, the request handler (success / non-200 / timeout / connection-error / NDJSON / non-JSON), and streaming (OpenAI / Anthropic / Ollama) now covered (`test_proxy_helpers.py`, `test_proxy_handler.py`). Remaining ~10% is defensive exception handlers, optional-codec returns, and lifespan startup |
| `dashboard.py` | 🟢 81% | control-plane API — **was 68%**; settings (GET/PATCH/DELETE/smtp-test), DLP allow-list rules, DLP-alert triage HTTP layer, profile self-service, user unlock, and the metrics/configuration snapshots now covered with their RBAC gates (`test_dashboard_endpoints.py`). Remaining ~19% is export-endpoint error branches, scattered edge branches, and HTML page template constants |
| `auth.py` | 🟢 100% | password hashing / policy / temp-password — **was 33%** (`test_auth.py`) |
| `settings.py` | 🟢 100% | runtime config resolver (DB→env→default) + validators — **was 62%** (`test_settings.py`) |
| `notifications.py` / `smtp_sender.py` | 🟢 100% / 100% | DLP-alert email delivery — **was 16% / 28%**; trigger-policy matrix, retry/cap state machine, the three encryption modes, and template rendering (`test_notifications.py`, `test_smtp_sender.py`) |
| `config.py` / `migrations` | 🟡 62–70% | config + schema setup |

Low-value, intentionally untested: `commands.py` (10%, CLI entrypoints),
`proxy.py` (0%, 4-line shim).

## Frontend — logic covered, views not

Pure logic and shared primitives are near-complete:

- `lib/` ~88% (most files 100%): `format`, `host-format`, `serial-ids`,
  `agent-names`, `request-kind`, `session-names`, `utils`
- `hooks/use-me` 100%, `use-debounced` covered; `api/client` covered
- Shared components tested: `status-badge`, `trust-score`, `relative-time`,
  `require-admin`, `mcp-server-dialog`, `users-dialog`, `date-range-picker`

Untested (0%): every `pages/*.tsx` route component and `pages/settings/*.tsx`
(sessions, agents, compliance, network-map, threats-alerts, etc.), plus
`use-prefetch.ts` and the `action-types.ts` lookup table.

---

## Priorities

1. ✅ **`signing.py`** — the contract is locked for the Rust port; coverage here
   guards the byte-level invariants ([signing contract](../README.md)). **Done**
   to the realistic ceiling (59%): key generation/loading, Ed25519 round-trip,
   ECDSA-P256 verify, TPM dispatch, and `get_configuration_info` are covered.
   The uncovered remainder is `TpmSigner` hardware I/O.
2. ✅ **`dlp.py` / `dlp_triage.py`** — core detection. **Done** (99% / 100%).
3. ✅ **`server.py` request paths** — the data plane itself. **Done** (90%).
   Request handler, streaming (OpenAI/Anthropic/Ollama), and the
   routing/normalization helpers are covered; the rest is defensive glue.
4. Frontend page components are the largest count gap but the lowest risk —
   defer behind the backend security modules. ← next, if pursued

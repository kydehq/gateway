"""
Behavioral Ledger — append-only, hash-chained, signed entry store.

Each entry contains:
  - who:          agent_id (from X-Agent-ID header or inferred)
  - what:         action type (chat, tool_call, tool_result, etc.)
  - why:          the reasoning context (last user/system messages)
  - inputs:       hashed representation of what went in
  - output:       hashed representation of what came out
  - tool_calls:   extracted tool invocations (highest signal)
  - prev_hash:    SHA-256 of previous entry (chain integrity)
  - signature:    Ed25519 over all fields above
  - client_ip:    IP address of the caller
  - user_agent:   User-Agent header of the caller
  - session_id:   logical session identifier
  - upstream:     upstream provider name
  - full_messages: complete message history from the request

The chain means you cannot silently delete or alter a past entry —
any modification breaks every subsequent hash link.

Storage: Postgres 16. `why`, `tool_calls`, `full_messages`, `findings`, and
`roles` are stored as JSONB so the dashboard can filter inside them with
indexed SQL instead of Python loops. Connection reuse is handled by a
module-level `psycopg_pool.ConnectionPool` — DATABASE_URL from env.
"""

import hashlib
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

import importlib.util as _importlib_util

from . import migrations

# Signing is an enterprise feature and ships as a removable module. When absent
# (sandbox edition) the ledger still writes — entries are hash-chained
# (tamper-evident) but carry an empty signature instead of an independent
# Ed25519/TPM attestation. Guarded locally rather than via `_features` to
# keep `ledger` the lowest layer and avoid an import cycle. We gate on
# find_spec so a *present-but-broken* signing module surfaces loudly
# instead of silently downgrading to unsigned (a security regression).
_HAS_SIGNING = _importlib_util.find_spec("kyde.signing") is not None
if _HAS_SIGNING:
    from .signing import sign_payload, verify_payload

if TYPE_CHECKING:
    from .network_origin import NetworkOrigin

GENESIS_HASH = "0" * 64  # sentinel for first entry

# Stable 64-bit identifier for the append-serialization advisory lock.
# Any constant works as long as every `append()` call uses the same one.
# ASCII of "LEDGER_1".
_APPEND_LOCK_KEY = 0x4C45444745525F31

_DEFAULT_DATABASE_URL = "postgresql://witness:witness-dev-only@postgres:5432/witness"

# Concatenated-column expression used by the trigram search index. Must
# match the expression in migrations/sql/0001_baseline.sql exactly so the
# query planner picks up the index. Update both if you add/remove a
# searchable field.
_SEARCH_EXPR = (
    "(coalesce(agent_id,'') || ' ' || coalesce(model,'') || ' ' || "
    "coalesce(entry_id,'') || ' ' || coalesce(client_ip,'') || ' ' || "
    "coalesce(session_id,''))"
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    entry_id: str
    timestamp: float
    agent_id: str
    action_type: str  # chat | tool_call | tool_result | error
    model: str
    why: list[dict]  # last N messages (reasoning context)
    input_hash: str  # SHA-256 of full request body
    output_hash: str  # SHA-256 of full response body
    tool_calls: list[dict]  # extracted tool invocations
    prev_hash: str  # hash of previous entry
    entry_hash: str = ""  # SHA-256 of this entry's canonical form
    signature: str = ""  # Ed25519 signature
    # ---- enriched metadata (stored alongside chain; covered by input_hash) ----
    client_ip: str = ""
    user_agent: str = ""
    session_id: str = ""
    upstream: str = ""
    full_messages: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Derived classifier label — see server.REQUEST_KIND_* and migration
    # 0010_request_kind.sql. Deliberately NOT in _signable(): the kind is
    # interpretation, so revising the classifier later mustn't invalidate
    # existing signed entries. Defaults to 'unknown' when callers don't
    # specify (e.g. older code paths).
    request_kind: str = "unknown"
    # DB-assigned row id, populated by append() via RETURNING seq. Zero until
    # the row is persisted; callers use it to attach side-table rows
    # (e.g. request_network) without a follow-up SELECT.
    seq: int = 0


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_dict(d: dict) -> str:
    # Sorted keys + no whitespace = canonical form for hashing.
    import json

    canonical = json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
    return _hash_bytes(canonical)


def _signable(entry: LedgerEntry) -> dict:
    """Fields that are signed — excludes entry_hash and signature themselves."""
    return {
        "entry_id": entry.entry_id,
        "timestamp": entry.timestamp,
        "agent_id": entry.agent_id,
        "action_type": entry.action_type,
        "model": entry.model,
        "why": entry.why,
        "input_hash": entry.input_hash,
        "output_hash": entry.output_hash,
        "tool_calls": entry.tool_calls,
        "prev_hash": entry.prev_hash,
    }


# ---------------------------------------------------------------------------
# Connection pool + schema
# ---------------------------------------------------------------------------

_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()
_schema_ready = False


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


# Pool sizing — overridable via env so operators can tune for their
# proxy workload without a code change. Tier 1 load tests showed the
# default 10-slot pool caps practical per-proxy throughput well below
# what the agent_traffic_meters UPSERT itself can sustain
# (see scripts/loadtest_traffic_db.results.md). Reasonable production
# values are 25–100 depending on chat:non-chat traffic mix.
_DEFAULT_POOL_MIN = 2
_DEFAULT_POOL_MAX = 10


def _pool_min() -> int:
    raw = os.environ.get("KYDE_DB_POOL_MIN", "")
    try:
        v = int(raw) if raw else _DEFAULT_POOL_MIN
    except ValueError:
        v = _DEFAULT_POOL_MIN
    return max(1, v)


def _pool_max() -> int:
    raw = os.environ.get("KYDE_DB_POOL_MAX", "")
    try:
        v = int(raw) if raw else _DEFAULT_POOL_MAX
    except ValueError:
        v = _DEFAULT_POOL_MAX
    return max(_pool_min(), v)


def _get_pool() -> ConnectionPool:
    """Lazily create a shared pool on first use.

    We initialize lazily rather than at import time so tools that import this
    module without a running Postgres (e.g. `--help` CLI invocations) don't
    blow up.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=_database_url(),
                    min_size=_pool_min(),
                    max_size=_pool_max(),
                    kwargs={"row_factory": dict_row},
                    open=True,
                )
                _pool.wait(timeout=30)
                _init_schema()
    return _pool


def _conn():
    """Yield a pooled connection. Use as a context manager."""
    return _get_pool().connection()


def _init_schema() -> None:
    """Apply pending migrations. Idempotent; safe to call repeatedly."""
    global _schema_ready
    if _schema_ready:
        return
    assert _pool is not None
    migrations.run(_pool)
    _schema_ready = True


# SQL for the historical request_network backfill. The baseline migration runs
# this once; operators (and tests) can re-run it via `backfill_request_network()`
# to repopulate rows that were lost or arrived before the side table existed.
_BACKFILL_REQUEST_NETWORK_SQL = """
DO $$
BEGIN
    INSERT INTO request_network (
        seq, timestamp, remote_addr, origin_ip, origin_class, origin_subnet
    )
    SELECT l.seq,
           l.timestamp,
           NULLIF(l.client_ip, '')::inet,
           NULLIF(l.client_ip, '')::inet,
           CASE
             WHEN l.client_ip = '' THEN 'unknown'
             WHEN NULLIF(l.client_ip,'')::inet << inet '127.0.0.0/8' THEN 'loopback'
             WHEN NULLIF(l.client_ip,'')::inet << inet '10.0.0.0/8'
               OR NULLIF(l.client_ip,'')::inet << inet '172.16.0.0/12'
               OR NULLIF(l.client_ip,'')::inet << inet '192.168.0.0/16' THEN 'rfc1918'
             WHEN NULLIF(l.client_ip,'')::inet << inet '100.64.0.0/10' THEN 'cgnat'
             WHEN NULLIF(l.client_ip,'')::inet << inet '169.254.0.0/16' THEN 'link_local'
             WHEN family(NULLIF(l.client_ip,'')::inet) = 6
              AND NULLIF(l.client_ip,'')::inet << inet 'fc00::/7' THEN 'unique_local_v6'
             ELSE 'public'
           END,
           COALESCE(
             host(network(set_masklen(NULLIF(l.client_ip,'')::inet,
                  CASE WHEN family(NULLIF(l.client_ip,'')::inet)=4 THEN 24 ELSE 48 END)))
             || CASE WHEN family(NULLIF(l.client_ip,'')::inet)=4 THEN '/24' ELSE '/48' END,
             '')
      FROM ledger l
     WHERE NOT EXISTS (SELECT 1 FROM request_network r WHERE r.seq = l.seq)
       AND l.client_ip <> ''
       AND LENGTH(l.client_ip) BETWEEN 3 AND 45
       AND l.client_ip ~ '^[0-9a-fA-F:.]+$';
EXCEPTION WHEN others THEN
    RAISE NOTICE 'request_network backfill skipped: %', SQLERRM;
END $$;
"""


def backfill_request_network() -> None:
    """Populate request_network for ledger rows missing a side-table entry.

    Runs once automatically as part of the baseline migration. Re-invoke
    after restoring ledger data from a backup or when adding the side table
    to a pre-existing deployment.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_BACKFILL_REQUEST_NETWORK_SQL)
        conn.commit()


# ---------------------------------------------------------------------------
# Ledger: append + query
# ---------------------------------------------------------------------------


def append(
    agent_id: str,
    action_type: str,
    model: str,
    request_body: dict,
    response_body: dict,
    why_messages: list[dict],
    tool_calls: list[dict],
    client_ip: str = "",
    user_agent: str = "",
    session_id: str = "",
    upstream: str = "",
    full_messages: Optional[list[dict]] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    request_kind: str = "unknown",
) -> LedgerEntry:
    """Create, sign, and persist a ledger entry. Returns the stored entry.

    Serialized across processes via a Postgres transaction-scoped advisory
    lock — avoids the TOCTOU race between reading the chain tip and inserting
    the new row.
    """
    entry = LedgerEntry(
        entry_id=str(uuid.uuid4()),
        timestamp=time.time(),
        agent_id=agent_id,
        action_type=action_type,
        model=model,
        why=why_messages,
        input_hash=_hash_dict(request_body),
        output_hash=_hash_dict(response_body),
        tool_calls=tool_calls,
        prev_hash=GENESIS_HASH,  # filled in under the lock below
        client_ip=client_ip,
        user_agent=user_agent,
        session_id=session_id,
        upstream=upstream,
        full_messages=full_messages or [],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        request_kind=request_kind,
    )

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_APPEND_LOCK_KEY,))
            cur.execute("SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1")
            row = cur.fetchone()
            entry.prev_hash = row["entry_hash"] if row else GENESIS_HASH

            signable = _signable(entry)
            entry.entry_hash = _hash_dict(signable)
            # Hash chain is edition-independent; the signature is enterprise-only.
            entry.signature = sign_payload(signable) if _HAS_SIGNING else ""

            cur.execute(
                """
                INSERT INTO ledger
                  (entry_id, timestamp, agent_id, action_type, model,
                   why, input_hash, output_hash, tool_calls,
                   prev_hash, entry_hash, signature,
                   client_ip, user_agent, session_id, upstream, full_messages,
                   prompt_tokens, completion_tokens, request_kind)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING seq
                """,
                (
                    entry.entry_id,
                    entry.timestamp,
                    entry.agent_id,
                    entry.action_type,
                    entry.model,
                    Jsonb(entry.why),
                    entry.input_hash,
                    entry.output_hash,
                    Jsonb(entry.tool_calls),
                    entry.prev_hash,
                    entry.entry_hash,
                    entry.signature,
                    entry.client_ip,
                    entry.user_agent,
                    entry.session_id,
                    entry.upstream,
                    Jsonb(entry.full_messages),
                    entry.prompt_tokens,
                    entry.completion_tokens,
                    entry.request_kind,
                ),
            )
            returned = cur.fetchone()
            if returned is not None:
                entry.seq = int(returned["seq"])
        conn.commit()
    global _LEDGER_VERSION
    with _VERIFY_CACHE_LOCK:
        _LEDGER_VERSION += 1
    return entry


def record_request_network(seq: int, timestamp: float, origin: "NetworkOrigin") -> None:
    """Insert network-origin enrichment for a ledger row.

    Called from server._log_entry after ledger.append returns; the ledger
    row's hash chain is already sealed, so this is a pure side-effect table
    insert. Pass through the entry's timestamp so time-window joins against
    `ledger.timestamp` are exact. Silently no-ops on seq == 0 (append failed)
    or a duplicate seq (PK conflict from a double call on the same entry).
    """
    if seq <= 0:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO request_network (
                    seq, timestamp,
                    remote_addr, forwarded_chain,
                    forwarded_for_raw, forwarded_raw, via_raw,
                    origin_ip, origin_class, origin_subnet,
                    ua_tool, ua_version, ua_os,
                    upstream_host, upstream_region
                ) VALUES (
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (seq) DO NOTHING
                """,
                (
                    seq,
                    timestamp,
                    origin.remote_addr,
                    Jsonb(origin.forwarded_chain),
                    origin.forwarded_for_raw,
                    origin.forwarded_raw,
                    origin.via_raw,
                    origin.origin_ip,
                    origin.origin_class,
                    origin.origin_subnet,
                    origin.ua_tool,
                    origin.ua_version,
                    origin.ua_os,
                    origin.upstream_host,
                    origin.upstream_region,
                ),
            )
        conn.commit()


def list_entries(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ledger ORDER BY seq DESC LIMIT %s", (limit,))
            return list(cur.fetchall())


def get_entry(entry_id: str) -> Optional[dict]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ledger WHERE entry_id = %s", (entry_id,))
            row = cur.fetchone()
            return row if row else None


# ---------------------------------------------------------------------------
# Verify chain — incremental cache
# ---------------------------------------------------------------------------
#
# The ledger is append-only and chain-linked. Once rows 1..N are known good,
# they cannot become invalid (signatures and prev_hash are immutable, and the
# chain prefix is fixed). So per-call work is "verify rows past last known-good
# seq" rather than a full-table re-verify. A short TTL on top absorbs
# concurrent polls within a single process.


@dataclass
class _VerifyCacheEntry:
    valid: bool
    errors: list[str]
    total: int
    verified: int
    chain_breaks: int
    sig_failures: int
    first_broken_seq: Optional[int]
    last_seq: int  # high-water seq we've verified through
    last_prev_hash: str  # entry_hash of last verified row (chain tip)
    cached_at: float
    version: int  # _LEDGER_VERSION snapshot


_VERIFY_CACHE: Optional[_VerifyCacheEntry] = None
_VERIFY_CACHE_LOCK = threading.Lock()
_LEDGER_VERSION = 0
_VERIFY_TTL_S = 5.0


def _reset_verify_cache() -> None:
    """Test hook — drop the in-process cache and reset the version counter.

    Production code should never call this; the conftest truncation fixture
    uses it to keep tests deterministic across the module-level cache.
    """
    global _VERIFY_CACHE, _LEDGER_VERSION
    with _VERIFY_CACHE_LOCK:
        _VERIFY_CACHE = None
        _LEDGER_VERSION = 0


def verify_chain(record: bool = True) -> tuple[bool, list[str]]:
    """
    Walk the ledger verifying:
      1. Each entry's signature is valid
      2. Each entry's prev_hash matches the previous entry's entry_hash
    Returns (all_valid, list_of_error_messages).

    When `record=True` (the default), persists the run to
    `verification_runs` so the Compliance screen can render a real
    verification history. Pass `record=False` for offline checks that
    shouldn't pollute the audit trail.

    Incremental: only rows past the last cached high-water mark are
    re-verified. A 5-second TTL absorbs concurrent polls.
    """
    global _VERIFY_CACHE

    now = time.time()
    with _VERIFY_CACHE_LOCK:
        cached = _VERIFY_CACHE
        current_version = _LEDGER_VERSION

    if (
        cached is not None
        and cached.version == current_version
        and (now - cached.cached_at) < _VERIFY_TTL_S
    ):
        if record:
            _record_verification_run(
                total_entries=cached.total,
                verified_entries=cached.verified,
                chain_breaks=cached.chain_breaks,
                signature_failures=cached.sig_failures,
                first_broken_seq=cached.first_broken_seq,
                status="pass" if cached.valid else "fail",
                error_sample=cached.errors[:10],
            )
        return (cached.valid, list(cached.errors))

    if cached is None:
        last_seq = 0
        prev_hash = GENESIS_HASH
        errors: list[str] = []
        chain_breaks = 0
        sig_failures = 0
        first_broken_seq: Optional[int] = None
    else:
        last_seq = cached.last_seq
        prev_hash = cached.last_prev_hash
        errors = list(cached.errors)
        chain_breaks = cached.chain_breaks
        sig_failures = cached.sig_failures
        first_broken_seq = cached.first_broken_seq

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seq, entry_id, timestamp, agent_id, action_type, model,
                       why, input_hash, output_hash, tool_calls,
                       prev_hash, entry_hash, signature
                  FROM ledger
                 WHERE seq > %s
                 ORDER BY seq ASC
                """,
                (last_seq,),
            )
            new_rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM ledger")
            total = int(cur.fetchone()["n"])

    for r in new_rows:
        signable = {
            "entry_id": r["entry_id"],
            "timestamp": r["timestamp"],
            "agent_id": r["agent_id"],
            "action_type": r["action_type"],
            "model": r["model"],
            "why": r["why"],
            "input_hash": r["input_hash"],
            "output_hash": r["output_hash"],
            "tool_calls": r["tool_calls"],
            "prev_hash": r["prev_hash"],
        }
        entry_ok = True
        if r["prev_hash"] != prev_hash:
            errors.append(
                f"[{r['entry_id']}] Chain break: expected prev_hash {prev_hash[:16]}… "
                f"got {r['prev_hash'][:16]}…"
            )
            chain_breaks += 1
            entry_ok = False
        # Unsigned entries (sandbox edition, or empty signature) are
        # chain-verified only — a missing independent signature is not a
        # tamper failure. Signature checks run only when signing shipped
        # and the row actually carries one.
        if _HAS_SIGNING and r["signature"]:
            if not verify_payload(signable, r["signature"]):
                errors.append(f"[{r['entry_id']}] Invalid signature")
                sig_failures += 1
                entry_ok = False
        if not entry_ok and first_broken_seq is None:
            first_broken_seq = int(r["seq"])
        prev_hash = r["entry_hash"]
        last_seq = int(r["seq"])

    valid = len(errors) == 0
    verified = total - chain_breaks - sig_failures
    if verified < 0:
        verified = 0

    with _VERIFY_CACHE_LOCK:
        _VERIFY_CACHE = _VerifyCacheEntry(
            valid=valid,
            errors=errors,
            total=total,
            verified=verified,
            chain_breaks=chain_breaks,
            sig_failures=sig_failures,
            first_broken_seq=first_broken_seq,
            last_seq=last_seq,
            last_prev_hash=prev_hash,
            cached_at=time.time(),
            version=current_version,
        )

    if record:
        _record_verification_run(
            total_entries=total,
            verified_entries=verified,
            chain_breaks=chain_breaks,
            signature_failures=sig_failures,
            first_broken_seq=first_broken_seq,
            status="pass" if valid else "fail",
            error_sample=errors[:10],
        )

    return (valid, errors)


def _record_verification_run(
    total_entries: int,
    verified_entries: int,
    chain_breaks: int,
    signature_failures: int,
    first_broken_seq: Optional[int],
    status: str,
    error_sample: list[str],
) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO verification_runs (
                    total_entries, verified_entries, chain_breaks,
                    signature_failures, first_broken_seq, status, error_sample
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    total_entries,
                    verified_entries,
                    chain_breaks,
                    signature_failures,
                    first_broken_seq,
                    status,
                    Jsonb(error_sample),
                ),
            )
        conn.commit()


def list_verification_runs(limit: int = 30) -> list[dict]:
    """Return verification runs newest-first for the Compliance screen."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, run_at, total_entries, verified_entries,
                       chain_breaks, signature_failures, first_broken_seq,
                       signature_alg, status, error_sample
                  FROM verification_runs
                 ORDER BY run_at DESC
                 LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Dashboard-facing read helpers (replace raw SQL in dashboard.py)
# ---------------------------------------------------------------------------


def count_entries() -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM ledger")
            return cur.fetchone()["c"]


def database_size_bytes() -> int:
    """Size of the current Postgres database on disk, in bytes."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database()) AS s")
            return cur.fetchone()["s"]


def get_stats_rows(since: Optional[float] = None) -> list[dict]:
    """Rows needed by /api/stats — scanned fully to produce aggregates.

    `since` (epoch seconds) filters to rows with timestamp >= since. Pass
    None to return all rows.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            if since is None:
                cur.execute(
                    """
                    SELECT agent_id, session_id, action_type, upstream, timestamp
                      FROM ledger
                     ORDER BY seq ASC
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT agent_id, session_id, action_type, upstream, timestamp
                      FROM ledger
                     WHERE timestamp >= %s
                     ORDER BY seq ASC
                    """,
                    (since,),
                )
            return list(cur.fetchall())


def count_rows(table: str) -> int:
    """COUNT(*) for a whitelisted table name.

    Used by the telemetry emitter to report adoption counts (how many MCP
    servers / DLP rules / users a deployment has) without a bespoke helper
    per table. The table name is checked against a fixed allowlist so it can
    never be attacker-influenced into arbitrary SQL.
    """
    allowed = {
        "mcp_servers",
        "dlp_rules",
        "dlp_prevention_patterns",
        "agent_traffic_meters",
        "users",
    }
    if table not in allowed:
        raise ValueError(f"count_rows: {table!r} not in allowlist")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
            return cur.fetchone()["c"]


def count_dlp_alerts_grouped(since: Optional[float] = None) -> list[dict]:
    """DLP alert counts grouped by label dimensions, windowed on created_at.

    Returns rows of {disposition, severity, scanner, source_type, prevented,
    n}. Powers the telemetry `usage.dlp_alerts_by_*` breakdowns. Only labels
    and counts — never the finding content. `since` (epoch seconds) filters
    to created_at >= since; None = all rows.
    """
    where = "WHERE created_at >= %s" if since is not None else ""
    params: list[Any] = [since] if since is not None else []
    sql = f"""
        SELECT COALESCE(disposition, '')  AS disposition,
               severity                   AS severity,
               scanner                    AS scanner,
               source_type                AS source_type,
               prevented                  AS prevented,
               COUNT(*)                   AS n
          FROM dlp_alerts
          {where}
         GROUP BY disposition, severity, scanner, source_type, prevented
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def get_dlp_tier2_features(since: Optional[float] = None) -> dict:
    """Content-DERIVED DLP features for Tier 2 telemetry (consent-gated).

    Returns {pattern_hits, entity_types, categories, score_histogram} — all
    aggregate counts extracted from the findings metadata (pattern_id /
    entity_type / label / category) and the alert score. The raw
    `matched_value` / `text` / `context_snippet` are NEVER read here: the
    SELECT lists only the non-sensitive derived keys.

    `since` (epoch seconds) filters on created_at; None = all rows.
    """
    where = "WHERE da.created_at >= %s" if since is not None else ""
    params: list[Any] = [since] if since is not None else []
    # Per-finding derived labels. COALESCE prefers pattern_id, then the
    # entity/label fallbacks the regex scanner emits, so a hit is always
    # attributable to *some* rule without ever touching the matched text.
    findings_sql = f"""
        SELECT COALESCE(NULLIF(f->>'pattern_id', ''),
                        NULLIF(f->>'entity_type', ''),
                        NULLIF(f->>'label', ''),
                        '(unknown)')          AS pattern,
               NULLIF(f->>'entity_type', '')  AS entity_type,
               NULLIF(f->>'category', '')     AS category,
               COUNT(*)                       AS n
          FROM dlp_alerts da,
               LATERAL jsonb_array_elements(da.findings) f
          {where}
         GROUP BY pattern, entity_type, category
    """
    # Score histogram: ten buckets across [0, 1]. width_bucket returns 1..10
    # for in-range scores and 11 for exactly 1.0, folded back into the top
    # bucket below.
    hist_sql = f"""
        SELECT width_bucket(da.score, 0.0, 1.0, 10) AS bucket, COUNT(*) AS n
          FROM dlp_alerts da
          {where}
         GROUP BY bucket
    """
    pattern_hits: dict[str, int] = {}
    entity_types: dict[str, int] = {}
    categories: dict[str, int] = {}
    score_histogram: dict[str, int] = {}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(findings_sql, params)
            for row in cur.fetchall():
                n = int(row["n"])
                pattern_hits[row["pattern"]] = pattern_hits.get(row["pattern"], 0) + n
                if row["entity_type"]:
                    entity_types[row["entity_type"]] = (
                        entity_types.get(row["entity_type"], 0) + n
                    )
                if row["category"]:
                    categories[row["category"]] = categories.get(row["category"], 0) + n
            cur.execute(hist_sql, params)
            for row in cur.fetchall():
                b = min(int(row["bucket"]), 10)  # fold the 1.0 edge into bucket 10
                lo = (b - 1) / 10.0
                hi = b / 10.0
                key = f"{lo:.1f}-{hi:.1f}"
                score_histogram[key] = score_histogram.get(key, 0) + int(row["n"])
    return {
        "pattern_hits": pattern_hits,
        "entity_types": entity_types,
        "categories": categories,
        "score_histogram": score_histogram,
    }


def get_telemetry_state() -> dict:
    """Return the singleton telemetry_state row (see 0021 migration)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT hmac_salt, last_sent, last_status, last_error, updated_at
                  FROM telemetry_state
                 WHERE singleton = TRUE
                """
            )
            row = cur.fetchone()
            return dict(row) if row else {}


def ensure_telemetry_salt() -> bytes:
    """Return the per-deploy HMAC salt, generating it once if absent.

    Atomic against concurrent callers: the UPDATE ... WHERE hmac_salt IS NULL
    only writes if the salt is still unset, and we re-read to return whatever
    salt actually won the race. Kept in the DB (not a file) so it lives with
    the watermark it protects.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT hmac_salt FROM telemetry_state WHERE singleton = TRUE")
            row = cur.fetchone()
            if row and row["hmac_salt"]:
                return bytes(row["hmac_salt"])
            fresh = os.urandom(32)
            cur.execute(
                """
                UPDATE telemetry_state
                   SET hmac_salt = %s, updated_at = %s
                 WHERE singleton = TRUE AND hmac_salt IS NULL
                """,
                (fresh, time.time()),
            )
            conn.commit()
            cur.execute("SELECT hmac_salt FROM telemetry_state WHERE singleton = TRUE")
            return bytes(cur.fetchone()["hmac_salt"])


def set_telemetry_last_sent(last_sent: float, status: str, error: str = "") -> None:
    """Advance the telemetry watermark after an emit attempt.

    Callers pass the new watermark only on success; on failure they pass the
    unchanged prior watermark so the same window is retried next cycle.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telemetry_state
                   SET last_sent = %s, last_status = %s,
                       last_error = %s, updated_at = %s
                 WHERE singleton = TRUE
                """,
                (last_sent, status, error, time.time()),
            )
            conn.commit()


def list_entries_for_api(limit: int = 500) -> list[dict]:
    """Unpaginated, unfiltered newest-first entries (legacy)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ledger ORDER BY seq DESC LIMIT %s", (limit,))
            return list(cur.fetchall())


def list_entries_paginated(
    limit: int = 50,
    cursor: Optional[int] = None,
    action: Optional[str] = None,
    upstream: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    search: Optional[str] = None,
    since: Optional[float] = None,
) -> dict:
    """Cursor-paginated entries feed.

    `cursor` is the `seq` of the last row from the previous page — the next
    page starts at `seq < cursor` (newest-first). Filters are AND-combined.
    `search` is a case-insensitive substring match served by the pg_trgm
    GIN index on the concatenated searchable-field expression.

    Returns `{items, next_cursor, has_more}`. `has_more` is detected by
    over-fetching one row beyond the limit.
    """
    # Cursor isn't part of the total-count filter — total should reflect
    # the full filtered set, not the current paginated window.
    filter_clauses: list[str] = []
    filter_params: list[Any] = []
    if action:
        filter_clauses.append("action_type = %s")
        filter_params.append(action)
    if upstream:
        filter_clauses.append("upstream = %s")
        filter_params.append(upstream)
    if agent_id:
        filter_clauses.append("agent_id = %s")
        filter_params.append(agent_id)
    if session_id:
        filter_clauses.append("session_id = %s")
        filter_params.append(session_id)
    if search:
        filter_clauses.append(f"{_SEARCH_EXPR} ILIKE %s")
        filter_params.append(f"%{search}%")
    if since is not None:
        filter_clauses.append("timestamp >= %s")
        filter_params.append(since)

    page_clauses = list(filter_clauses)
    page_params = list(filter_params)
    if cursor is not None:
        page_clauses.append("seq < %s")
        page_params.append(cursor)

    where_page = f"WHERE {' AND '.join(page_clauses)}" if page_clauses else ""
    where_count = f"WHERE {' AND '.join(filter_clauses)}" if filter_clauses else ""
    sql_page = f"SELECT * FROM ledger {where_page} ORDER BY seq DESC LIMIT %s"
    sql_count = f"SELECT COUNT(*) AS c FROM ledger {where_count}"

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_page, page_params + [limit + 1])
            rows = list(cur.fetchall())
            cur.execute(sql_count, filter_params)
            total = int(cur.fetchone()["c"])

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["seq"] if items and has_more else None
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total_count": total,
    }


def entry_facets() -> dict:
    """Distinct `action_type` and non-empty `upstream` values, sorted — used
    to populate the Timeline filter dropdowns.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT action_type FROM ledger "
                "WHERE action_type <> '' ORDER BY action_type"
            )
            actions = [r["action_type"] for r in cur.fetchall()]
            cur.execute(
                "SELECT DISTINCT upstream FROM ledger "
                "WHERE upstream <> '' ORDER BY upstream"
            )
            upstreams = [r["upstream"] for r in cur.fetchall()]
    return {"actions": actions, "upstreams": upstreams}


_SESSION_SORTS: dict[str, str] = {
    "newest": "last_time DESC",
    "oldest": "first_time ASC",
    "entries": "entry_count DESC, last_time DESC",
    "agents": "agent_count DESC, last_time DESC",
}


def list_session_summaries(
    limit: int = 50,
    cursor: Optional[float] = None,
    since: Optional[float] = None,
    has_alert: Optional[str] = None,  # None / "yes" / "no"
    agents: Optional[list[str]] = None,
    sort: str = "newest",
    status: Optional[list[str]] = None,  # subset of {blocked, observed, allowed}
) -> dict:
    """One-row-per-session roll-up.

    Filters:
      - since:      only include sessions whose last_time >= since
      - has_alert:  "yes" → only sessions with ≥1 non-closed dlp_alert;
                    "no"  → only sessions with zero non-closed alerts;
                    None  → no filter
      - agents:     keep only sessions involving any of these agent_ids
      - sort:       one of newest / oldest / entries / agents
                    (default newest = last_time DESC)
      - status:     subset of {blocked, observed, allowed}. None means no
                    filter. Status is derived:
                      blocked  = session has ≥1 action_type='policy_block'
                      observed = no block but ≥1 non-closed dlp_alert
                      allowed  = neither

    `cursor` is the previous page's last_time. Cursor pagination is well-
    defined for sort='newest'; for the other sorts the cursor still works
    but the resulting page order may not match the global ORDER BY across
    pages. The UI disables infinite scroll past the first page when sort
    isn't 'newest'.

    Empty session_id rows are always skipped.
    """
    where_clauses: list[str] = ["l.session_id <> ''"]
    where_params: list[Any] = []
    if agents:
        where_clauses.append(
            "l.session_id IN ("
            "SELECT DISTINCT session_id FROM ledger "
            "WHERE agent_id = ANY(%s) AND session_id <> ''"
            ")"
        )
        where_params.append(agents)

    having_clauses: list[str] = []
    having_params: list[Any] = []
    if since is not None:
        having_clauses.append("MAX(l.timestamp) >= %s")
        having_params.append(since)
    if cursor is not None:
        having_clauses.append("MAX(l.timestamp) < %s")
        having_params.append(cursor)
    if has_alert == "yes":
        having_clauses.append(
            "EXISTS (SELECT 1 FROM dlp_alerts da "
            "WHERE da.session_id = l.session_id AND da.status <> 'closed')"
        )
    elif has_alert == "no":
        having_clauses.append(
            "NOT EXISTS (SELECT 1 FROM dlp_alerts da "
            "WHERE da.session_id = l.session_id AND da.status <> 'closed')"
        )

    # Status filter is HAVING-level — it depends on aggregates and on a
    # correlated subquery for the dlp_alerts side.
    status_set = set(status or [])
    valid_statuses = {"blocked", "observed", "allowed"}
    if status_set and not status_set.issubset(valid_statuses):
        raise ValueError(
            f"invalid status values: {status_set - valid_statuses}; "
            f"expected subset of {sorted(valid_statuses)}"
        )

    if status_set and status_set != valid_statuses:
        # Translate the requested status set into a HAVING predicate. We
        # evaluate three pieces and OR them together:
        block_expr = "BOOL_OR(l.action_type = 'policy_block')"
        alert_expr = (
            "EXISTS (SELECT 1 FROM dlp_alerts da WHERE da.session_id = l.session_id "
            "AND da.status <> 'closed')"
        )
        ors: list[str] = []
        if "blocked" in status_set:
            ors.append(f"({block_expr})")
        if "observed" in status_set:
            ors.append(f"(NOT {block_expr} AND {alert_expr})")
        if "allowed" in status_set:
            ors.append(f"(NOT {block_expr} AND NOT {alert_expr})")
        if ors:
            having_clauses.append("(" + " OR ".join(ors) + ")")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    having_sql = ("HAVING " + " AND ".join(having_clauses)) if having_clauses else ""
    order_by = _SESSION_SORTS.get(sort, _SESSION_SORTS["newest"])

    sql = f"""
        SELECT l.session_id,
               s.serial_id,
               COUNT(*)                       AS entry_count,
               COUNT(DISTINCT l.agent_id)     AS agent_count,
               array_agg(DISTINCT l.agent_id) AS agents,
               MIN(l.timestamp)               AS first_time,
               MAX(l.timestamp)               AS last_time,
               BOOL_OR(l.action_type = 'policy_block') AS has_block,
               EXISTS (
                   SELECT 1 FROM dlp_alerts da
                    WHERE da.session_id = l.session_id
                      AND da.status <> 'closed'
               )                              AS has_open_alert
          FROM ledger l
          LEFT JOIN sessions s ON s.session_id = l.session_id
         {where_sql}
         GROUP BY l.session_id, s.serial_id
         {having_sql}
         ORDER BY {order_by}
         LIMIT %s
    """
    params: list[Any] = [*where_params, *having_params, limit + 1]

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1]["last_time"] if items and has_more else None
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


# Agent block-list functions (is_agent_blocked / block_agent / unblock_agent
# / list_agent_blocks) are an enterprise enforcement feature and now live in
# kyde/enforce/blocklist.py — physically absent from the sandbox image.
# The `agent_blocks` table itself stays in core (every edition migrates it).


# ---------------------------------------------------------------------------
# Host resolutions — reverse-DNS cache + admin labels. See host_resolver.py
# for the resolution strategy. These helpers are pure DB access; the
# admin-precedence + TTL freshness logic lives in the resolver.
# ---------------------------------------------------------------------------


def get_host_resolution(ip: str) -> Optional[dict]:
    """Return the cached row for `ip`, or None. Caller decides freshness."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ip, hostname, source, resolved_at, ttl_seconds"
                "  FROM host_resolutions WHERE ip = %s",
                (ip,),
            )
            return cur.fetchone()


def upsert_host_label(ip: str, hostname: str, by_user_id: Optional[int] = None) -> dict:
    """Admin-supplied label. Wins over DNS — host_resolver never overwrites
    an 'admin' row. Empty/whitespace hostname is rejected; use
    `delete_host_label` to clear."""
    normalized = (hostname or "").strip()
    if not normalized:
        raise ValueError("hostname is required; use delete_host_label to clear")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO host_resolutions (ip, hostname, source, resolved_at, ttl_seconds)
                VALUES (%s, %s, 'admin', now(), 0)
                ON CONFLICT (ip) DO UPDATE
                   SET hostname = EXCLUDED.hostname,
                       source = 'admin',
                       resolved_at = now(),
                       ttl_seconds = 0
                RETURNING ip, hostname, source, resolved_at, ttl_seconds
                """,
                (ip, normalized),
            )
            row = cur.fetchone()
        conn.commit()
    # by_user_id is intentionally unused — we don't track the author of an
    # admin label in v1. If you need provenance later, add a column to
    # host_resolutions rather than threading the id through this signature.
    _ = by_user_id
    return row


def delete_host_label(ip: str) -> bool:
    """Remove an admin label. DNS will repopulate on the next read. Returns
    True if a row was removed."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM host_resolutions WHERE ip = %s AND source = 'admin'",
                (ip,),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def upsert_host_dns(ip: str, hostname: Optional[str], ttl_seconds: int) -> None:
    """DNS-sourced cache write. NO-OP if an admin row already exists for
    this IP — admin precedence is enforced here, not at the caller, so the
    resolver doesn't need to special-case it."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO host_resolutions (ip, hostname, source, resolved_at, ttl_seconds)
                VALUES (%s, %s, 'dns', now(), %s)
                ON CONFLICT (ip) DO UPDATE
                   SET hostname = EXCLUDED.hostname,
                       resolved_at = now(),
                       ttl_seconds = EXCLUDED.ttl_seconds
                 WHERE host_resolutions.source = 'dns'
                """,
                (ip, hostname, int(ttl_seconds)),
            )
        conn.commit()


def list_host_labels() -> list[dict]:
    """Rows where the admin set the label explicitly. Drives the
    Settings 'Labeled' chip."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ip, hostname, resolved_at, ttl_seconds
                  FROM host_resolutions
                 WHERE source = 'admin'
                 ORDER BY resolved_at DESC
                """
            )
            return list(cur.fetchall())


def list_host_resolutions(
    *,
    status: str = "all",
    search: Optional[str] = None,
    recently_active_since: Optional[float] = None,
    limit: int = 100,
) -> list[dict]:
    """One row per observed IP for the Settings Host Names table.

    Returned shape per row:
      {ip, hostname, source, resolved_at, ttl_seconds, last_seen}
    where `last_seen` (epoch seconds) is the MAX(request_network.timestamp)
    for that IP. None when the IP has been resolved but never observed
    on the network (rare; can happen if an admin pre-labels an IP).

    `status`:
      'labeled'         → IPs with source='admin'
      'unlabeled'       → IPs observed in request_network with no admin label
                          (may still have a dns-sourced hostname or no row)
      'recently_active' → observed within recently_active_since
      'all'             → union of labeled + observed
    """
    # Union-of-CTEs approach: pull observed IPs from request_network and
    # left-join host_resolutions so we get a single row per IP regardless
    # of which side it lives on. Empty/NULL remote_addr is filtered out.
    sql = """
        WITH observed AS (
            SELECT host(rn.remote_addr) AS ip,
                   MAX(rn.timestamp)    AS last_seen
              FROM request_network rn
             WHERE rn.remote_addr IS NOT NULL
             GROUP BY host(rn.remote_addr)
        ),
        labeled_only AS (
            SELECT hr.ip,
                   NULL::double precision AS last_seen
              FROM host_resolutions hr
             WHERE hr.source = 'admin'
               AND hr.ip NOT IN (SELECT ip FROM observed)
        ),
        all_ips AS (
            SELECT ip, last_seen FROM observed
            UNION ALL
            SELECT ip, last_seen FROM labeled_only
        )
        SELECT a.ip,
               hr.hostname,
               hr.source,
               hr.resolved_at,
               hr.ttl_seconds,
               a.last_seen
          FROM all_ips a
          LEFT JOIN host_resolutions hr ON hr.ip = a.ip
    """
    where: list[str] = []
    params: list[Any] = []
    if status == "labeled":
        where.append("hr.source = 'admin'")
    elif status == "unlabeled":
        # Unlabeled = no admin row. May still have dns or no row at all.
        where.append("(hr.source IS NULL OR hr.source <> 'admin')")
    elif status == "recently_active":
        if recently_active_since is None:
            raise ValueError(
                "recently_active_since required for status=recently_active"
            )
        where.append("a.last_seen >= %s")
        params.append(recently_active_since)
    elif status != "all":
        raise ValueError(f"invalid status {status!r}")

    if search:
        where.append("(a.ip ILIKE %s OR hr.hostname ILIKE %s)")
        params.append(f"%{search}%")
        params.append(f"%{search}%")

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY a.last_seen DESC NULLS LAST LIMIT %s"
    params.append(int(limit))

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def find_most_recent_ip_for_hostname(hostname: str) -> Optional[str]:
    """Reverse lookup: pick the IP most recently observed on the network
    among the IPs that resolve to `hostname`. Used by the host-page route
    when the URL identifier is a hostname rather than an IP."""
    if not hostname:
        return None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT hr.ip,
                       COALESCE(MAX(rn.timestamp), 0) AS last_seen
                  FROM host_resolutions hr
                  LEFT JOIN request_network rn ON host(rn.remote_addr) = hr.ip
                 WHERE hr.hostname = %s
                 GROUP BY hr.ip
                 ORDER BY last_seen DESC
                 LIMIT 1
                """,
                (hostname,),
            )
            row = cur.fetchone()
            return row["ip"] if row else None


def find_ips_for_hostname(hostname: str) -> list[dict]:
    """All IPs mapping to a hostname with last_seen, sorted most-recent
    first. Powers the multi-IP picker on the host page."""
    if not hostname:
        return []
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT hr.ip,
                       hr.source,
                       MAX(rn.timestamp) AS last_seen
                  FROM host_resolutions hr
                  LEFT JOIN request_network rn ON host(rn.remote_addr) = hr.ip
                 WHERE hr.hostname = %s
                 GROUP BY hr.ip, hr.source
                 ORDER BY last_seen DESC NULLS LAST
                """,
                (hostname,),
            )
            return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Per-agent traffic metering — see migration 0011_agent_traffic.sql and
# project memory project_per_agent_traffic_metering.md for the design.
# ---------------------------------------------------------------------------


TRAFFIC_MODE_COUNT_ONLY = "count_only"
TRAFFIC_MODE_FULL_LOGGING = "full_logging"
_VALID_TRAFFIC_MODES = (TRAFFIC_MODE_COUNT_ONLY, TRAFFIC_MODE_FULL_LOGGING)


# In-process cache of (agent_id, path_kind) → mode. The proxy hot path
# consults this on every non-chat request, so a per-request DB roundtrip
# would dominate. TTL is short (5s) so a mode flip via the dashboard
# propagates without explicit cross-process invalidation — fine for a
# multi-proxy deployment without pub/sub. set_agent_traffic_mode() below
# also invalidates the local entry, so single-process deployments see
# the change immediately.
_MODE_CACHE_TTL_SECONDS = 5.0
_mode_cache: dict[tuple[str, str], tuple[str, float]] = {}
_mode_cache_lock = threading.Lock()


def get_agent_traffic_mode_cached(agent_id: str, path_kind: str) -> str:
    """Same as get_agent_traffic_mode but cached with a 5s TTL. Used by
    the proxy on every non-chat request to decide whether to write a
    full ledger row. Cache miss falls through to a normal DB read.
    """
    key = (agent_id, path_kind)
    now = time.time()
    with _mode_cache_lock:
        cached = _mode_cache.get(key)
        if cached is not None and (now - cached[1]) < _MODE_CACHE_TTL_SECONDS:
            return cached[0]
    # Miss — read fresh and populate. Don't hold the lock across the
    # DB call so concurrent misses don't serialise.
    mode = get_agent_traffic_mode(agent_id, path_kind)
    with _mode_cache_lock:
        _mode_cache[key] = (mode, now)
    return mode


def _invalidate_mode_cache(agent_id: str, path_kind: str) -> None:
    with _mode_cache_lock:
        _mode_cache.pop((agent_id, path_kind), None)


def _clear_mode_cache() -> None:
    """Test helper — drop the entire cache."""
    with _mode_cache_lock:
        _mode_cache.clear()


def record_agent_traffic(agent_id: str, path_kind: str) -> None:
    """Increment the (agent_id, path_kind) counter and refresh last_seen.

    Idempotent UPSERT — runs on every proxy request, including blocked and
    non-chat. The PK index makes this cheap (~hundreds of µs). Errors are
    swallowed by the caller (best-effort) so a temporary DB hiccup never
    breaks the proxy hot path.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_traffic_meters (agent_id, path_kind, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (agent_id, path_kind) DO UPDATE
                  SET count = agent_traffic_meters.count + 1,
                      last_seen = now()
                """,
                (agent_id, path_kind),
            )
        conn.commit()


def get_agent_traffic_mode(agent_id: str, path_kind: str) -> str:
    """Return the latest mode for (agent_id, path_kind) or the default.

    Reads the newest row from agent_traffic_mode_history. Absence of any
    history row means the operator hasn't flipped this tuple — default is
    count_only.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mode
                  FROM agent_traffic_mode_history
                 WHERE agent_id = %s AND path_kind = %s
                 ORDER BY changed_at DESC
                 LIMIT 1
                """,
                (agent_id, path_kind),
            )
            row = cur.fetchone()
            if row:
                return row["mode"]
    return TRAFFIC_MODE_COUNT_ONLY


def set_agent_traffic_mode(
    agent_id: str,
    path_kind: str,
    mode: str,
    changed_by: Optional[int],
) -> dict:
    """Append a mode-flip row to agent_traffic_mode_history.

    Raises ValueError on an unknown mode value — caller (the dashboard
    endpoint) catches and returns 400. Returns the row that was written
    so the API can echo (id, changed_at) back to the client.
    """
    if mode not in _VALID_TRAFFIC_MODES:
        raise ValueError(
            f"invalid mode {mode!r}; expected one of {_VALID_TRAFFIC_MODES}"
        )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_traffic_mode_history
                  (agent_id, path_kind, mode, changed_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id, changed_at
                """,
                (agent_id, path_kind, mode, changed_by),
            )
            row = cur.fetchone()
        conn.commit()
    # Same-process consumers (single-proxy dev / test) see the change
    # immediately; multi-proxy deployments wait for the TTL to expire.
    _invalidate_mode_cache(agent_id, path_kind)
    return {
        "id": row["id"],
        "agent_id": agent_id,
        "path_kind": path_kind,
        "mode": mode,
        "changed_at": row["changed_at"],
        "changed_by": changed_by,
    }


def list_agent_traffic(agent_id: Optional[str] = None) -> list[dict]:
    """Return every (agent_id, path_kind) meter joined with its current mode.

    When agent_id is given, scope to that agent only. The current mode is
    the most-recent agent_traffic_mode_history row for the pair, defaulting
    to 'count_only' if none exists.
    """
    where = ""
    params: list = []
    if agent_id:
        where = "WHERE m.agent_id = %s"
        params.append(agent_id)
    sql = f"""
        SELECT
            m.agent_id,
            m.path_kind,
            m.count,
            m.first_seen,
            m.last_seen,
            COALESCE(
                (
                    SELECT mode FROM agent_traffic_mode_history h
                     WHERE h.agent_id = m.agent_id
                       AND h.path_kind = m.path_kind
                     ORDER BY h.changed_at DESC LIMIT 1
                ),
                '{TRAFFIC_MODE_COUNT_ONLY}'
            ) AS mode
          FROM agent_traffic_meters m
          {where}
         ORDER BY m.last_seen DESC
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def list_agents() -> list[dict]:
    """Return every known agent with display_name + activity rollups.

    Joins ledger to count entries and pick the dominant model. The hot-path
    proxy populates the `agents` table via trigger, so this is a cheap
    JOIN for the Settings UI's agent list.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.agent_id,
                       a.display_name,
                       a.first_seen,
                       a.last_seen,
                       COUNT(l.seq)                       AS entry_count,
                       COUNT(DISTINCT l.session_id)
                           FILTER (WHERE l.session_id <> '') AS session_count
                  FROM agents a
                  LEFT JOIN ledger l ON l.agent_id = a.agent_id
                 GROUP BY a.agent_id, a.display_name, a.first_seen, a.last_seen
                 ORDER BY a.last_seen DESC
                """
            )
            return list(cur.fetchall())


def set_agent_display_name(agent_id: str, display_name: Optional[str]) -> bool:
    """Update the admin-supplied display name. Pass None to clear it.

    Returns True if a row was updated. The frontend's getAgentDisplayName()
    falls back to a hash-derived label when display_name is NULL.
    """
    normalized = display_name.strip() if display_name else None
    if normalized == "":
        normalized = None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agents SET display_name = %s WHERE agent_id = %s",
                (normalized, agent_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def get_session_serial_id(session_id: str) -> Optional[int]:
    """Look up the monotonic serial_id for a session, or None if unseen."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT serial_id FROM sessions WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()
    return int(row["serial_id"]) if row else None


def get_session_detail(session_id: str, limit: int = 500) -> list[dict]:
    """Return entries for a single session, oldest-first (reading order).
    Uses the `ledger_session_idx` btree.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entry_id, seq, timestamp, session_id, agent_id, model,
                       upstream, action_type, why, tool_calls, full_messages,
                       prompt_tokens, completion_tokens, client_ip
                  FROM ledger
                 WHERE session_id = %s
                 ORDER BY seq ASC
                 LIMIT %s
                """,
                (session_id, limit),
            )
            return list(cur.fetchall())


def get_entry_by_ref(ref: str) -> Optional[dict]:
    """Look up an entry by seq (int-like string), entry_id, or entry_id prefix."""
    with _conn() as conn:
        with conn.cursor() as cur:
            if ref.isdigit():
                cur.execute("SELECT * FROM ledger WHERE seq = %s", (int(ref),))
                row = cur.fetchone()
                if row:
                    return row
            cur.execute("SELECT * FROM ledger WHERE entry_id = %s", (ref,))
            row = cur.fetchone()
            if row:
                return row
            cur.execute(
                "SELECT * FROM ledger WHERE entry_id LIKE %s ORDER BY seq ASC LIMIT 1",
                (ref + "%",),
            )
            return cur.fetchone()


def get_metrics_rows() -> list[dict]:
    """Minimal columns for /api/metrics KPI rollups."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT timestamp, action_type FROM ledger ORDER BY seq ASC")
            return list(cur.fetchall())


def get_session_rows() -> list[dict]:
    """Rows for /api/sessions. Includes `why` + `tool_calls` since the
    auditor view surfaces the last prompt snippet and tool invocation counts.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entry_id, seq, timestamp, session_id, agent_id, model,
                       upstream, action_type, why, tool_calls, full_messages,
                       prompt_tokens, completion_tokens
                  FROM ledger
                 ORDER BY seq ASC
                """
            )
            return list(cur.fetchall())


def get_token_analysis_rows(
    since: Optional[float] = None,
    agent_id: Optional[str] = None,
) -> list[dict]:
    """Rows for /api/token-analysis — per-entry tokens + agent/model/upstream.

    `since` (epoch seconds) restricts to rows with timestamp >= since. Pass
    None to scan the entire ledger.

    `agent_id` scopes the query to one agent — the per-agent token section
    on the Agent page uses this so a single agent's usage can be rendered
    without summing client-side over every other agent's rows.
    """
    base_sql = """
        SELECT l.timestamp, l.agent_id, l.model, l.upstream,
               l.prompt_tokens, l.completion_tokens
          FROM ledger l
        """
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append("l.timestamp >= %s")
        params.append(since)
    if agent_id:
        clauses.append("l.agent_id = %s")
        params.append(agent_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(base_sql + f" {where} ORDER BY l.seq ASC", params)
            return list(cur.fetchall())


def get_dlp_alerts_by_session(session_id: str) -> dict[str, list[dict]]:
    """Return {entry_id: [alert_summary, ...]} for one session.

    Lightweight projection — the Sessions screen only needs enough to
    render a per-entry banner and link to the detail sheet, not the full
    alert payload. The dlp_alerts table is small relative to ledger, so a
    single round-trip per session detail page is acceptable.
    """
    out: dict[str, list[dict]] = {}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT alert_id, id AS serial_id, entry_id, severity, status,
                       disposition, score, scanner, created_at
                  FROM dlp_alerts
                 WHERE session_id = %s
                 ORDER BY created_at ASC
                """,
                (session_id,),
            )
            for r in cur.fetchall():
                out.setdefault(r["entry_id"], []).append(
                    {
                        "alert_id": r["alert_id"],
                        "serial_id": int(r["serial_id"]),
                        "severity": r["severity"],
                        "status": r["status"],
                        "disposition": r["disposition"],
                        "score": float(r["score"]) if r["score"] is not None else 0.0,
                        "scanner": r["scanner"],
                    }
                )
    return out


# ---------------------------------------------------------------------------
# DLP Alerts API
# ---------------------------------------------------------------------------


def _dedup_hash(scanner: str, findings: list[dict]) -> str:
    """Fingerprint a DLP finding so the same leak re-detected across
    successive entries (because LLM history carries prior prompts forward)
    collapses into one open alert.

    Scope is (scanner, normalized finding content). Deliberately excludes
    session_id and entry_id — those are what we're deduping across.
    """
    import json as _json

    parts: list[str] = [scanner]
    if scanner == "regex":
        # Per-match key: entity type + normalized matched text. Sorted so
        # match order from the scanner doesn't change the hash.
        keys: list[str] = []
        for m in findings or []:
            etype = str(m.get("entity_type") or m.get("pattern_name") or "")
            text = str(m.get("text") or m.get("value") or "").strip().lower()
            keys.append(f"{etype}:{text}")
        parts.extend(sorted(keys))
    elif scanner == "bert":
        # BERT is a classifier — no span, only a label.
        label = (findings[0].get("label") if findings else "") or ""
        parts.append(str(label))
    else:
        # Unknown scanner: fall back to canonical-JSON of the payload.
        parts.append(_json.dumps(findings, sort_keys=True, separators=(",", ":")))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
_SEV_BY_RANK = {v: k for k, v in _SEV_RANK.items()}


def _rollup_severity(findings: list[dict]) -> str:
    """Highest severity across a finding set. Defaults to MEDIUM when no
    finding carries one — matches the legacy column default, uppercased to
    align with what YAML rules produce."""
    best = 0
    for f in findings or []:
        s = (f.get("severity") or "").upper()
        best = max(best, _SEV_RANK.get(s, 0))
    return _SEV_BY_RANK.get(best, "MEDIUM")


def upsert_dlp_alert(
    entry_id: str,
    session_id: str,
    scanner: str,
    score: float,
    findings: list[dict],
    *,
    source_type: str = "chat",
    mcp_server_id: Optional[str] = None,
    mcp_method: Optional[str] = None,
    mcp_tool_name: Optional[str] = None,
    prevented: bool = False,
) -> tuple[dict, bool]:
    """Insert a new dlp_alert, OR bump the seen_count of an existing OPEN
    alert with the same dedup_hash. Returns (row, is_new); is_new=False
    means this scan was a repeat of an already-open alert.

    Closed alerts do NOT block new rows — that's what the partial unique
    index `dlp_alerts_dedup_open_idx` enforces (predicate: status <> 'closed').
    Any non-closed lifecycle state (new, claimed, in_progress, pending_info,
    escalated) absorbs repeat detections into the existing parent alert.

    source_type / mcp_* columns flag MCP findings so the triage UI can split
    them under a "Source" filter without a parallel alerts table. They're
    keyword-only so the chat-DLP call sites keep their original shape.

    prevented=True marks alerts whose request was blocked inline (DLP
    prevention). On a dedup hit it's promoted but never demoted — like
    severity: once a leak cluster has caused a block, it stays marked.
    """
    now = time.time()
    dedup = _dedup_hash(scanner, findings)
    new_sev = _rollup_severity(findings)

    with _conn() as conn:
        with conn.cursor() as cur:
            # Row-lock any existing open alert so two concurrent scans
            # can't both fall through to INSERT and race the unique index.
            cur.execute(
                """
                SELECT id, severity FROM dlp_alerts
                 WHERE dedup_hash = %s
                   AND status <> 'closed'
                 FOR UPDATE
                """,
                (dedup,),
            )
            existing = cur.fetchone()

            if existing:
                # Dedup hit. Bump counters, re-mark for email pickup, and
                # clear any prior failure state so the worker retries cleanly.
                # Severity is promoted but never demoted: a worse rule on a
                # repeat detection raises the parent alert, a milder one
                # does not lower it.
                old_rank = _SEV_RANK.get((existing["severity"] or "").upper(), 0)
                merged_sev = (
                    new_sev if _SEV_RANK[new_sev] > old_rank else existing["severity"]
                )
                cur.execute(
                    """
                    UPDATE dlp_alerts
                       SET seen_count         = seen_count + 1,
                           last_seen_entry_id = %s,
                           last_seen_at       = %s,
                           updated_at         = %s,
                           score              = GREATEST(score, %s),
                           severity           = %s,
                           prevented          = prevented OR %s,
                           email_status       = 'pending',
                           email_attempts     = 0,
                           email_last_error   = ''
                     WHERE id = %s
                     RETURNING *
                    """,
                    (entry_id, now, now, score, merged_sev, prevented, existing["id"]),
                )
                row = cur.fetchone()
                conn.commit()
                return (row or {}), False

            alert_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO dlp_alerts
                  (alert_id, entry_id, session_id, scanner, score, findings,
                   status, dedup_hash, severity,
                   last_seen_entry_id, last_seen_at, seen_count,
                   email_status,
                   created_at, updated_at,
                   source_type, mcp_server_id, mcp_method, mcp_tool_name,
                   prevented)
                VALUES
                  (%s, %s, %s, %s, %s, %s,
                   'new', %s, %s,
                   %s, %s, 1,
                   'pending',
                   %s, %s,
                   %s, %s, %s, %s,
                   %s)
                RETURNING *
                """,
                (
                    alert_id,
                    entry_id,
                    session_id,
                    scanner,
                    score,
                    Jsonb(findings),
                    dedup,
                    new_sev,
                    entry_id,
                    now,
                    now,
                    now,
                    source_type,
                    mcp_server_id,
                    mcp_method,
                    mcp_tool_name,
                    prevented,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return (row or {}), True


def get_prior_full_messages_length(session_id: str, seq: int) -> int:
    """Return the length of `full_messages` on the closest prior entry in
    the same session, or 0 if this is the first entry. Used to split the
    entry's `full_messages` into "context inherited from earlier turns"
    vs. "what this entry contributed" without shipping the prior payload.

    policy_block rows are excluded: a blocked request never reached the
    upstream, so its messages were never "shipped" — counting them would
    let a client retry of the same payload compute a delta that skips the
    re-sent secret and sail past inline DLP prevention."""
    if not session_id:
        return 0
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT full_messages
                  FROM ledger
                 WHERE session_id = %s AND seq < %s
                   AND action_type <> 'policy_block'
                 ORDER BY seq DESC
                 LIMIT 1
                """,
                (session_id, seq),
            )
            row = cur.fetchone()
            if not row:
                return 0
            msgs = row.get("full_messages") or []
            return len(msgs) if isinstance(msgs, list) else 0


def get_prior_full_messages_length_for_session(session_id: str) -> int:
    """Like get_prior_full_messages_length, but bounded only by session —
    for the inline prevention path, where the current request has no
    ledger entry (and thus no seq) yet. Same policy_block exclusion."""
    if not session_id:
        return 0
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT full_messages
                  FROM ledger
                 WHERE session_id = %s
                   AND action_type <> 'policy_block'
                 ORDER BY seq DESC
                 LIMIT 1
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return 0
            msgs = row.get("full_messages") or []
            return len(msgs) if isinstance(msgs, list) else 0


def get_dlp_alerts_for_entry(entry_id: str) -> list[dict]:
    """Return all dlp_alerts for one ledger entry, shaped like rows from
    `list_dlp_alerts` (same field names + derived `findings_parsed`,
    `serial_id`, `agent_id`, `created_dt`). Used by the entry-detail
    payload so the dialog can render alerts + highlight matching
    messages without an extra round-trip per alert."""
    from datetime import datetime

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM dlp_alerts WHERE entry_id = %s ORDER BY id ASC",
                (entry_id,),
            )
            rows = cur.fetchall()
            agent_id = ""
            if rows:
                cur.execute(
                    "SELECT agent_id FROM ledger WHERE entry_id = %s LIMIT 1",
                    (entry_id,),
                )
                er = cur.fetchone()
                if er:
                    agent_id = er["agent_id"]

    for r in rows:
        r["findings_parsed"] = r["findings"]
        r["created_dt"] = datetime.fromtimestamp(r["created_at"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        r["serial_id"] = r["id"]
        r["agent_id"] = agent_id
    return rows


def get_dlp_alert(alert_id: str) -> Optional[dict]:
    """Return one dlp_alerts row by alert_id, shaped like an entry from
    `list_dlp_alerts` (same field names + derived `findings_parsed`,
    `serial_id`, `agent_id`, `created_dt`). Returns None if not found."""
    from datetime import datetime

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT da.*, ms.name AS mcp_server_name
                  FROM dlp_alerts da
             LEFT JOIN mcp_servers ms ON ms.id = da.mcp_server_id
                 WHERE da.alert_id = %s
                """,
                (alert_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            agent_id = ""
            if row.get("entry_id"):
                cur.execute(
                    "SELECT agent_id FROM ledger WHERE entry_id = %s LIMIT 1",
                    (row["entry_id"],),
                )
                er = cur.fetchone()
                if er:
                    agent_id = er["agent_id"]

    row["findings_parsed"] = row["findings"]
    row["created_dt"] = datetime.fromtimestamp(row["created_at"]).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    row["serial_id"] = row["id"]
    row["agent_id"] = agent_id
    if row.get("mcp_server_id") is not None:
        row["mcp_server_id"] = str(row["mcp_server_id"])
    return row


def list_dlp_alerts(
    limit: int = 200,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
) -> list[dict]:
    """Return dlp_alerts ordered newest-first.

    Optional filters:
      - status: one of the triage lifecycle values ("new", "in_review", ...)
      - source_type: "chat" or "mcp" — splits the chat-DLP feed from the
        MCP-DLP feed for the triage page's source filter.
    """
    from datetime import datetime

    where_clauses: list[str] = []
    params: list = []
    if status:
        where_clauses.append("da.status = %s")
        params.append(status)
    if source_type:
        where_clauses.append("da.source_type = %s")
        params.append(source_type)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT da.*, ms.name AS mcp_server_name
                  FROM dlp_alerts da
             LEFT JOIN mcp_servers ms ON ms.id = da.mcp_server_id
                {where_sql}
              ORDER BY da.id DESC
                 LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    if not rows:
        return rows

    # Look up agent_id per entry in one round trip so the frontend can wire
    # "Block this agent" off an alert without an extra fetch per row.
    entry_ids = list({r["entry_id"] for r in rows if r["entry_id"]})
    agent_by_entry: dict[str, str] = {}
    if entry_ids:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT entry_id, agent_id FROM ledger WHERE entry_id = ANY(%s)",
                    (entry_ids,),
                )
                for er in cur.fetchall():
                    agent_by_entry[er["entry_id"]] = er["agent_id"]

    for r in rows:
        # JSONB already comes back parsed; keep the legacy key name for clients.
        r["findings_parsed"] = r["findings"]
        r["created_dt"] = datetime.fromtimestamp(r["created_at"]).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        # Expose the BIGSERIAL `id` under the canonical `serial_id` name so the
        # frontend can use one formatter across alerts, sessions, and incidents.
        r["serial_id"] = r["id"]
        r["agent_id"] = agent_by_entry.get(r["entry_id"], "")
        if r.get("mcp_server_id") is not None:
            r["mcp_server_id"] = str(r["mcp_server_id"])
    return rows


# ---------------------------------------------------------------------------
# Users & authentication
# ---------------------------------------------------------------------------

VALID_ROLES = {"admin", "viewer", "auditor"}
LOCKOUT_THRESHOLD = 3


def _row_to_user(row: Optional[dict]) -> Optional[dict]:
    """Normalize a users row — derive `locked` / `deleted` flags, scrub hash."""
    if row is None:
        return None
    d = dict(row)
    d["locked"] = d.get("locked_at") is not None
    d["deleted"] = d.get("deleted_at") is not None
    # Never leak the password hash through user-facing helpers.
    d.pop("password_hash", None)
    return d


def any_admin_exists() -> bool:
    """True if any non-deleted user has the 'admin' role."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM users
                     WHERE deleted_at IS NULL
                       AND roles ? 'admin'
                ) AS has_admin
                """
            )
            return bool(cur.fetchone()["has_admin"])


def count_active_admins(exclude_user_id: Optional[int] = None) -> int:
    """Count non-deleted, enabled users with the admin role.

    `exclude_user_id` lets callers reason about "would there still be an admin
    if I removed this one?".
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS c FROM users
                 WHERE deleted_at IS NULL
                   AND enabled
                   AND roles ? 'admin'
                   AND (%s::bigint IS NULL OR id <> %s::bigint)
                """,
                (exclude_user_id, exclude_user_id),
            )
            return cur.fetchone()["c"]


def create_user(
    username: str,
    email: str,
    password_hash: str,
    roles: list[str],
    must_change_password: bool = True,
) -> dict:
    """Insert a new user. Raises psycopg.errors.UniqueViolation on username collision."""
    invalid = [r for r in roles if r not in VALID_ROLES]
    if invalid:
        raise ValueError(f"invalid roles: {invalid}")
    if not roles:
        raise ValueError("at least one role required")
    now = time.time()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (
                    username, email, password_hash, roles,
                    enabled, must_change_password, failed_login_count, locked_at,
                    created_at, last_login_at, password_changed_at, deleted_at
                ) VALUES (%s,%s,%s,%s, TRUE,%s, 0, NULL, %s, NULL, %s, NULL)
                RETURNING *
                """,
                (
                    username,
                    email,
                    password_hash,
                    Jsonb(sorted(set(roles))),
                    must_change_password,
                    now,
                    now,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_user(row)  # type: ignore[return-value]


def _fetch_user_row(where: str, params: tuple, include_deleted: bool) -> Optional[dict]:
    sql = f"SELECT * FROM users WHERE {where}"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def get_user_by_username(
    username: str, include_deleted: bool = False
) -> Optional[dict]:
    row = _fetch_user_row("username = %s", (username,), include_deleted)
    return _row_to_user(row)


def get_user_by_id(user_id: int, include_deleted: bool = False) -> Optional[dict]:
    row = _fetch_user_row("id = %s", (user_id,), include_deleted)
    return _row_to_user(row)


def get_password_hash(user_id: int) -> Optional[str]:
    """Return the stored hash for a user (bypasses _row_to_user's scrubbing)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return row["password_hash"] if row else None


def list_users(include_deleted: bool = False) -> list[dict]:
    sql = "SELECT * FROM users"
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    sql += " ORDER BY id ASC"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    return [_row_to_user(r) for r in rows]  # type: ignore[misc]


def get_auditor_emails() -> list[str]:
    """Return distinct emails of enabled, non-deleted users whose roles
    include 'auditor'. Used by the SMTP notification worker.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT email
                  FROM users
                 WHERE enabled
                   AND deleted_at IS NULL
                   AND email <> ''
                   AND roles @> '["auditor"]'::jsonb
                 ORDER BY email ASC
                """
            )
            rows = cur.fetchall()
    return [r["email"] for r in rows]


_UPDATABLE_FIELDS = {"email", "roles", "enabled"}


def update_user(user_id: int, **fields: Any) -> Optional[dict]:
    """Update a whitelisted set of columns. Returns the fresh row, or None if not found."""
    cleaned: dict = {}
    for k, v in fields.items():
        if k not in _UPDATABLE_FIELDS:
            raise ValueError(f"field not updatable: {k}")
        if k == "roles":
            invalid = [r for r in v if r not in VALID_ROLES]
            if invalid:
                raise ValueError(f"invalid roles: {invalid}")
            if not v:
                raise ValueError("at least one role required")
            cleaned[k] = Jsonb(sorted(set(v)))
        elif k == "enabled":
            cleaned[k] = bool(v)
        else:
            cleaned[k] = v
    if not cleaned:
        return get_user_by_id(user_id, include_deleted=True)
    set_clause = ", ".join(f"{k} = %s" for k in cleaned.keys())
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {set_clause} WHERE id = %s",
                (*cleaned.values(), user_id),
            )
        conn.commit()
    return get_user_by_id(user_id, include_deleted=True)


def soft_delete_user(user_id: int) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET deleted_at = %s, enabled = FALSE WHERE id = %s",
                (time.time(), user_id),
            )
        conn.commit()


def set_password(user_id: int, password_hash: str) -> None:
    """Store a new hash, clear must_change_password, update timestamp."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                   SET password_hash = %s,
                       password_changed_at = %s,
                       must_change_password = FALSE
                 WHERE id = %s
                """,
                (password_hash, time.time(), user_id),
            )
        conn.commit()


def set_temp_password(user_id: int, password_hash: str) -> None:
    """Store a hash AND force a change on next login (admin-issued temp)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                   SET password_hash = %s,
                       password_changed_at = %s,
                       must_change_password = TRUE,
                       failed_login_count = 0,
                       locked_at = NULL
                 WHERE id = %s
                """,
                (password_hash, time.time(), user_id),
            )
        conn.commit()


def record_login_success(user_id: int) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                   SET failed_login_count = 0,
                       last_login_at = %s
                 WHERE id = %s
                """,
                (time.time(), user_id),
            )
        conn.commit()


def record_login_failure(user_id: int) -> int:
    """Increment failure count; lock the account once it hits the threshold.

    Returns the new failure count.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT failed_login_count FROM users WHERE id = %s", (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return 0
            new_count = (row["failed_login_count"] or 0) + 1
            if new_count >= LOCKOUT_THRESHOLD:
                cur.execute(
                    "UPDATE users SET failed_login_count = %s, locked_at = %s WHERE id = %s",
                    (new_count, time.time(), user_id),
                )
            else:
                cur.execute(
                    "UPDATE users SET failed_login_count = %s WHERE id = %s",
                    (new_count, user_id),
                )
        conn.commit()
    return new_count


def unlock_user(user_id: int) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET locked_at = NULL, failed_login_count = 0 WHERE id = %s",
                (user_id,),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Dashboard auth sessions — DB-backed so cookies survive kyde-api restarts.
# Schema: migrations/sql/0018_auth_sessions.sql. The table is named
# `auth_sessions` to disambiguate from the pre-existing `sessions` table
# (ledger-conversation tracking, migration 0003).
# ---------------------------------------------------------------------------


_DEFAULT_SESSION_TTL_S = 86400  # matches the cookie max-age set in dashboard.py


def create_session(
    token: str,
    user_id: int,
    username: str,
    roles: list[str],
    must_change_password: bool,
    ttl_seconds: int = _DEFAULT_SESSION_TTL_S,
) -> None:
    """Persist a new session token. Idempotent on (token) via PK upsert."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auth_sessions
                       (token, user_id, username, roles,
                        must_change_password, expires_at)
                VALUES (%s, %s, %s, %s, %s, now() + (%s || ' seconds')::interval)
                ON CONFLICT (token) DO UPDATE
                  SET user_id              = EXCLUDED.user_id,
                      username             = EXCLUDED.username,
                      roles                = EXCLUDED.roles,
                      must_change_password = EXCLUDED.must_change_password,
                      expires_at           = EXCLUDED.expires_at
                """,
                (
                    token,
                    user_id,
                    username,
                    Jsonb(list(roles)),
                    bool(must_change_password),
                    str(int(ttl_seconds)),
                ),
            )
        conn.commit()


def get_session(token: str) -> Optional[dict]:
    """Return the session context dict the middleware expects, or None.

    Filters by expires_at so stale rows don't authenticate. Shape matches
    what SESSION_TOKENS used to store: user_id, username, roles list,
    must_change_password bool.
    """
    if not token:
        return None
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, username, roles, must_change_password
                  FROM auth_sessions
                 WHERE token = %s AND expires_at > now()
                """,
                (token,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "user_id": int(row["user_id"]),
        "username": row["username"],
        "roles": list(row["roles"] or []),
        "must_change_password": bool(row["must_change_password"]),
    }


def update_session_context(
    token: str,
    *,
    username: str,
    roles: list[str],
    must_change_password: bool,
) -> None:
    """Patch the denormalised fields after a self-service role/flag change.

    No-op if the token doesn't exist — the caller (`_refresh_session`)
    treats "row gone" as "session was invalidated" and forces re-login on
    the next request via the get_session miss.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_sessions
                   SET username             = %s,
                       roles                = %s,
                       must_change_password = %s
                 WHERE token = %s
                """,
                (
                    username,
                    Jsonb(list(roles)),
                    bool(must_change_password),
                    token,
                ),
            )
        conn.commit()


def delete_session(token: str) -> None:
    """Drop a single session (logout). Safe on already-deleted tokens."""
    if not token:
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM auth_sessions WHERE token = %s", (token,))
        conn.commit()


def delete_sessions_for_user(user_id: int, except_token: Optional[str] = None) -> int:
    """Drop every session for `user_id`. Returns the row count deleted.

    `except_token` keeps the caller's own session alive — used on
    role-change so the admin who just rotated their own roles doesn't get
    logged out by their own action.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            if except_token:
                cur.execute(
                    "DELETE FROM auth_sessions WHERE user_id = %s AND token <> %s",
                    (user_id, except_token),
                )
            else:
                cur.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
            count = cur.rowcount
        conn.commit()
    return int(count)


def list_sessions_for_user(user_id: int) -> list[str]:
    """Return live (non-expired) session tokens for a user. Test hook."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT token FROM auth_sessions
                 WHERE user_id = %s AND expires_at > now()
                """,
                (user_id,),
            )
            rows = cur.fetchall()
    return [r["token"] for r in rows]


# ---------------------------------------------------------------------------
# Runtime-tunable settings (DB-backed overrides for whitelisted env vars).
# The whitelist + validation lives in kyde/settings.py; this module
# only handles storage.
# ---------------------------------------------------------------------------


def get_setting(key: str) -> Optional[dict]:
    """Return the stored row for a setting, or None if unset."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, value, updated_at, updated_by FROM settings WHERE key = %s",
                (key,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def list_settings(keys: list[str]) -> dict[str, dict]:
    """Fetch every setting in `keys` in one round-trip. Missing → not in result."""
    if not keys:
        return {}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, value, updated_at, updated_by FROM settings WHERE key = ANY(%s)",
                (keys,),
            )
            rows = cur.fetchall()
    return {r["key"]: dict(r) for r in rows}


def upsert_setting(key: str, value: str, user_id: Optional[int]) -> dict:
    """Insert-or-update a setting row. Returns the stored row."""
    now = time.time()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings (key, value, updated_at, updated_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                  SET value = EXCLUDED.value,
                      updated_at = EXCLUDED.updated_at,
                      updated_by = EXCLUDED.updated_by
                RETURNING key, value, updated_at, updated_by
                """,
                (key, value, now, user_id),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)  # type: ignore[arg-type]


def delete_setting(key: str) -> bool:
    """Remove a setting row. Returns True if a row was deleted."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM settings WHERE key = %s", (key,))
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)


# ---------------------------------------------------------------------------
# DLP rules (allow / block)
# ---------------------------------------------------------------------------


def _normalize_match_text(text: Optional[str]) -> Optional[str]:
    """Lowercase + strip for comparison. `None` and empty both mean
    'any text' — we normalize both to None so the unique index treats
    them the same way."""
    if text is None:
        return None
    t = text.strip().lower()
    return t or None


def list_dlp_rules() -> list[dict]:
    """All rules, newest first. Joins username for display."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, u.username AS created_by_username
                  FROM dlp_rules r
                  LEFT JOIN users u ON u.id = r.created_by
                 ORDER BY r.id DESC
                """
            )
            rows = cur.fetchall()
    return list(rows)


def create_dlp_rule(
    kind: str,
    scanner: Optional[str],
    entity_type: str,
    match_text: Optional[str],
    note: str,
    user_id: Optional[int],
) -> dict:
    """Insert a rule. Raises ValueError on invalid input, or returns an
    error dict `{'error': 'duplicate'}` on unique-violation so the API
    layer can turn it into a 409 without catching psycopg exceptions.

    Normalization: entity_type and scanner are lowercased on insert so
    matching is case-insensitive for free without relying on functional
    indexes, and so duplicate-detection treats "EMAIL_ADDRESS" and
    "email_address" as the same rule.
    """
    if kind not in ("allow", "block"):
        raise ValueError(f"kind must be 'allow' or 'block', got {kind!r}")
    entity_type = (entity_type or "").strip().lower()
    if not entity_type:
        raise ValueError("entity_type is required")
    scanner = (scanner or "").strip().lower() or None
    if scanner is not None and scanner not in ("bert", "regex"):
        raise ValueError(f"scanner must be 'bert', 'regex', or null; got {scanner!r}")
    match_text = _normalize_match_text(match_text)
    note = (note or "").strip()
    now = time.time()

    import psycopg as _psycopg

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dlp_rules
                      (kind, scanner, entity_type, match_text, note,
                       created_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (kind, scanner, entity_type, match_text, note, user_id, now),
                )
                row = cur.fetchone()
            conn.commit()
        return row or {}
    except _psycopg.errors.UniqueViolation:
        return {"error": "duplicate"}


def delete_dlp_rule(rule_id: int) -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dlp_rules WHERE id = %s", (rule_id,))
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)


def find_and_bump_allow_rule(
    scanner: str,
    entity_type_candidates: list[str],
    match_text: Optional[str],
) -> Optional[int]:
    """If an allow-rule matches ANY of the finding's entity-type
    candidates, increment its hit_count and return the rule id. Otherwise
    return None. Single atomic statement — safe under concurrency.

    Regex findings don't have a single canonical "entity_type": the dlp-
    regex sidecar emits both `pattern_id` (stable slug, e.g.
    "sql_injection_attempt") and `pattern_name` (display, e.g. "SQL
    Injection Pattern"). Callers pass both — plus any legacy `entity_type`
    field — as candidates so the user can allowlist by whichever
    identifier they see in the UI.

    Matching is case-insensitive (LOWER both sides) because rule entries
    are typed manually by admins.

    Specificity: a rule with a non-NULL match_text beats one with NULL
    match_text (ORDER BY places more-specific first). The caller gets
    hit attribution on the precise rule rather than a broad umbrella.
    """
    # Normalize candidate list: lowercase, strip, drop empties, dedupe.
    norm_candidates: list[str] = []
    seen: set[str] = set()
    for c in entity_type_candidates:
        s = (c or "").strip().lower()
        if s and s not in seen:
            seen.add(s)
            norm_candidates.append(s)
    if not norm_candidates:
        return None

    norm_match = _normalize_match_text(match_text)
    now = time.time()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dlp_rules
                   SET hit_count   = hit_count + 1,
                       last_hit_at = %s
                 WHERE id = (
                     SELECT id FROM dlp_rules
                      WHERE kind = 'allow'
                        AND (scanner IS NULL OR LOWER(scanner) = LOWER(%s))
                        AND LOWER(entity_type) = ANY(%s::text[])
                        AND (match_text IS NULL OR match_text = %s)
                      ORDER BY (match_text IS NULL), id
                      LIMIT 1
                 )
                 RETURNING id
                """,
                (now, scanner, norm_candidates, norm_match),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["id"]) if row else None


# ---------------------------------------------------------------------------
# Session turn fingerprints — thread reconstruction
# ---------------------------------------------------------------------------


_SESSION_MATCH_WINDOW_SECONDS = 2 * 60 * 60  # 2h — stale entries don't merge


def find_session_by_turn_hashes(
    hashes: list[str],
    within_seconds: int = _SESSION_MATCH_WINDOW_SECONDS,
) -> Optional[str]:
    """Return the session_id that shares the MOST turn hashes with the
    incoming request (and has been active within `within_seconds`).
    Ties break by most-recent activity. Returns None if no session
    matches even a single hash.

    The caller (server._session_id) filters `hashes` down to substantive
    turns (length >= 20 chars) before calling, so a single match here is
    a strong signal — trivial content can't collide.
    """
    if not hashes:
        return None
    cutoff = time.time() - float(within_seconds)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id,
                       COUNT(*)           AS match_count,
                       MAX(first_seen)    AS latest
                  FROM session_turns
                 WHERE turn_hash = ANY(%s::text[])
                   AND first_seen >= %s
                 GROUP BY session_id
                 ORDER BY match_count DESC, latest DESC
                 LIMIT 1
                """,
                (hashes, cutoff),
            )
            row = cur.fetchone()
    return row["session_id"] if row else None


def record_session_turns(session_id: str, hashes: list[str]) -> None:
    """Persist (session_id, turn_hash) pairs so future requests can find
    this session by any of its turns. Idempotent — ON CONFLICT DO NOTHING.
    """
    if not session_id or not hashes:
        return
    now = time.time()
    rows = [(session_id, h, now) for h in hashes]
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO session_turns (session_id, turn_hash, first_seen)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id, turn_hash) DO NOTHING
                """,
                rows,
            )
        conn.commit()


# ---------------------------------------------------------------------------
# MCP routing — server registry + per-tool policy
# ---------------------------------------------------------------------------
#
# Pure storage. Caching, validation, and the most-specific-wins policy
# lookup live in mcp_registry.py / mcp_proxy.py respectively. No credential
# columns by design — see migrations/sql/0013_mcp_routing.sql.


def list_mcp_servers(tenant_id: str) -> list[dict]:
    """All registered MCP servers for a tenant, ordered by name.
    last_* columns ride along so dashboards can flag flaky upstreams
    without a separate query (migration 0016)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text AS id, tenant_id, name, upstream_url,
                       enabled, created_at, created_by,
                       last_call_at, last_error_at,
                       last_error_status, last_error_snippet
                FROM mcp_servers
                WHERE tenant_id = %s
                ORDER BY name
                """,
                (tenant_id,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_mcp_server(tenant_id: str, name: str) -> Optional[dict]:
    """Single server by (tenant_id, name), or None."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text AS id, tenant_id, name, upstream_url,
                       enabled, created_at, created_by,
                       last_call_at, last_error_at,
                       last_error_status, last_error_snippet
                FROM mcp_servers
                WHERE tenant_id = %s AND name = %s
                """,
                (tenant_id, name),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def upsert_mcp_server(
    tenant_id: str,
    name: str,
    upstream_url: str,
    enabled: bool,
    user_id: Optional[int],
) -> dict:
    """Insert-or-update a server keyed by (tenant_id, name)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_servers (tenant_id, name, upstream_url, enabled, created_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, name) DO UPDATE
                  SET upstream_url = EXCLUDED.upstream_url,
                      enabled      = EXCLUDED.enabled
                RETURNING id::text AS id, tenant_id, name, upstream_url,
                          enabled, created_at, created_by,
                          last_call_at, last_error_at,
                          last_error_status, last_error_snippet
                """,
                (tenant_id, name, upstream_url, enabled, user_id),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)  # type: ignore[arg-type]


def delete_mcp_server(tenant_id: str, name: str) -> bool:
    """Remove a server by (tenant_id, name). Cascades to mcp_tool_policies."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mcp_servers WHERE tenant_id = %s AND name = %s",
                (tenant_id, name),
            )
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)


def list_mcp_tool_policies(server_id: str) -> list[dict]:
    """All policy rows for a server. mcp_proxy applies most-specific-wins."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT server_id::text AS server_id, agent_id, tool_name,
                       decision, reason, updated_at, updated_by
                FROM mcp_tool_policies
                WHERE server_id = %s
                """,
                (server_id,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_mcp_tool_policy(
    server_id: str,
    agent_id: str,
    tool_name: str,
    decision: str,
    reason: Optional[str],
    user_id: Optional[int],
) -> dict:
    """Insert-or-update one (server, agent, tool) policy row."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_tool_policies
                    (server_id, agent_id, tool_name, decision, reason, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (server_id, agent_id, tool_name) DO UPDATE
                  SET decision   = EXCLUDED.decision,
                      reason     = EXCLUDED.reason,
                      updated_at = now(),
                      updated_by = EXCLUDED.updated_by
                RETURNING server_id::text AS server_id, agent_id, tool_name,
                          decision, reason, updated_at, updated_by
                """,
                (server_id, agent_id, tool_name, decision, reason, user_id),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row)  # type: ignore[arg-type]


def delete_mcp_tool_policy(server_id: str, agent_id: str, tool_name: str) -> bool:
    """Remove one policy row."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM mcp_tool_policies
                WHERE server_id = %s AND agent_id = %s AND tool_name = %s
                """,
                (server_id, agent_id, tool_name),
            )
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)

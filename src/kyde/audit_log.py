"""
Admin action audit log.

Every mutation through the admin-gated dashboard endpoints (MCP registry
CRUD, MCP policy CRUD, DLP policy toggle/resync, ...) lands as one row in
`admin_actions`. The before/after snapshots are the row dicts the registry
or DB helper handed back to the handler — already structured, no extra
query, no risk of joining against state that has since drifted.

Why a separate table from `ledger`:
  * Ledger entries are signed, append-only, and exported as evidence.
    Admin actions are operational telemetry — not part of the chain of
    custody, so a relational table is the right shape.
  * Filtering ("show me everything actor X did to MCP servers") is the
    primary access pattern and benefits from indexed columns.

Why denormalise `actor_username`:
  * When a user row is deleted the FK goes NULL but the audit trail
    must keep showing who did the thing. Standard forensic pattern.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from psycopg.types.json import Jsonb

from . import ledger

log = logging.getLogger(__name__)


def _json_or_null(value: Any) -> Any:
    """Wrap dict/list in Jsonb so psycopg sends jsonb; pass through None."""
    if value is None:
        return None
    return Jsonb(value)


def record(
    *,
    actor_id: Optional[int],
    actor_username: Optional[str],
    action: str,
    resource_type: str,
    resource_id: Optional[str],
    before: Optional[dict] = None,
    after: Optional[dict] = None,
) -> None:
    """Insert one audit row.

    Failures are logged but swallowed — a broken audit table must never
    take down the caller's primary operation (which has already
    succeeded by the time we get here).
    """
    try:
        with ledger._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO admin_actions (
                        actor_id, actor_username, action,
                        resource_type, resource_id, before, after
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        actor_id,
                        actor_username,
                        action,
                        resource_type,
                        resource_id,
                        _json_or_null(before),
                        _json_or_null(after),
                    ),
                )
            conn.commit()
    except Exception:
        log.exception(
            "failed to record admin action %s on %s/%s",
            action,
            resource_type,
            resource_id,
        )


def list_actions(
    *,
    limit: int = 100,
    offset: int = 0,
    actor_id: Optional[int] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
) -> dict:
    """Paginated audit query. Returns {items, total, limit, offset}.

    Filters are AND-combined. Newest first. `total` is computed under
    the same WHERE so dashboards can show "1 to 25 of 314".
    """
    where_parts: list[str] = []
    params: list[Any] = []
    if actor_id is not None:
        where_parts.append("actor_id = %s")
        params.append(actor_id)
    if action:
        where_parts.append("action = %s")
        params.append(action)
    if resource_type:
        where_parts.append("resource_type = %s")
        params.append(resource_type)
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with ledger._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS n FROM admin_actions {where_sql}",
                params,
            )
            total = int(cur.fetchone()["n"])

            cur.execute(
                f"""
                SELECT id, actor_id, actor_username, action,
                       resource_type, resource_id, before, after,
                       created_at
                FROM admin_actions
                {where_sql}
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()

    items = []
    for r in rows:
        items.append(
            {
                "id": int(r["id"]),
                "actor_id": r["actor_id"],
                "actor_username": r["actor_username"],
                "action": r["action"],
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "before": r["before"],
                "after": r["after"],
                "created_at": (
                    r["created_at"].isoformat() if r["created_at"] else None
                ),
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}

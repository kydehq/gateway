-- Normalize every legacy session_id to a canonical UUID v4.
--
-- Two legacy formats existed before this migration:
--   * `s-<uuid8>`             — minted for fresh conversations
--   * `session:<sha256[:16]>` — fallback for very short messages
-- Plus assorted hand-supplied `X-Session-ID` headers from clients.
--
-- Frontend formatters in lib/serial-ids.ts assumed a stable shape; the
-- migration to a single UUID format removes the prefix mess and makes the
-- column safe to format anywhere. New IDs are minted as `uuid.uuid4()` in
-- server.py — no prefix.
--
-- Strategy: collect every distinct non-UUID session_id across ledger,
-- session_turns, and dlp_alerts; mint a fresh UUID per legacy value into a
-- temp map; rewrite all three tables in one transaction. Empty strings are
-- left alone (they mean "no session" and the API contract preserves that).

CREATE TEMP TABLE session_id_map (
    old_id TEXT PRIMARY KEY,
    new_id TEXT NOT NULL
) ON COMMIT DROP;

INSERT INTO session_id_map (old_id, new_id)
SELECT DISTINCT s.session_id, gen_random_uuid()::text
FROM (
    SELECT session_id FROM ledger        WHERE session_id <> ''
    UNION
    SELECT session_id FROM session_turns WHERE session_id <> ''
    UNION
    SELECT session_id FROM dlp_alerts    WHERE session_id <> ''
) s
WHERE s.session_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';

UPDATE ledger l
   SET session_id = m.new_id
  FROM session_id_map m
 WHERE l.session_id = m.old_id;

UPDATE session_turns t
   SET session_id = m.new_id
  FROM session_id_map m
 WHERE t.session_id = m.old_id;

UPDATE dlp_alerts a
   SET session_id = m.new_id
  FROM session_id_map m
 WHERE a.session_id = m.old_id;

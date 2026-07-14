-- Collapse the DLP triage status enum from 6 values to 3.
--
-- Before: new | claimed | in_progress | pending_info | escalated | closed
-- After:  new | in_review | closed
--
-- The four intermediate triage states collapse to a single `in_review`
-- bucket — that matches what the live UI surfaces and removes the
-- mismatch between backend, types, and the page. `new` and `closed` are
-- preserved as-is.
--
-- Disposition values on historical closed rows are intentionally NOT
-- rewritten: the CHECK constraint (`dlp_alerts_disposition_ck`) only
-- requires NOT NULL when status='closed', not membership in any enum.
-- So legacy values like `benign_true_positive` / `policy_violation` /
-- `inconclusive` stay in the audit trail for historical accuracy; the
-- backend just stops listing them in DISPOSITIONS for new writes.

UPDATE dlp_alerts
   SET status     = 'in_review',
       updated_at = COALESCE(updated_at, EXTRACT(EPOCH FROM NOW()))
 WHERE status IN ('claimed', 'in_progress', 'pending_info', 'escalated');

-- The partial unique index `dlp_alerts_dedup_open_idx` from 0001 uses
-- `status <> 'closed'` so it doesn't need updating — `in_review` still
-- counts as open, same as the four states it replaces.

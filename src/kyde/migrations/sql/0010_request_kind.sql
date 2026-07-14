-- Add a request_kind label to every ledger row. The proxy only persists
-- chat-shaped rows today (_should_log_path filters non-chat traffic
-- entirely), so request_kind distinguishes *why* an entry has no visible
-- content rather than what kind of API endpoint it hit. Possible values
-- (string enum, validated in Python):
--
--   chat                      normal user/assistant content
--   chat_tool_only            assistant returned tool_calls, no content
--   chat_streaming_partial    SSE assembly returned empty body
--   chat_empty_request        request had no messages (or system-only)
--   chat_empty_content        messages present but every content == ""
--   policy_block              DLP/policy intervened; never went upstream
--   unknown                   classifier couldn't decide (backfilled rows)
--
-- request_kind is interpretation, NOT raw history. It is deliberately
-- excluded from _signable() in ledger.py so improving the classifier
-- later does not invalidate signatures on existing rows.

ALTER TABLE ledger
    ADD COLUMN IF NOT EXISTS request_kind TEXT;

-- Backfill from signals we already have on existing rows:
--   action_type='policy_block'              → policy_block
--   action_type='tool_call'                 → chat_tool_only
--   full_messages non-empty array           → chat
--   else                                    → unknown
UPDATE ledger
   SET request_kind = CASE
       WHEN action_type = 'policy_block'
            THEN 'policy_block'
       WHEN action_type = 'tool_call'
            THEN 'chat_tool_only'
       WHEN full_messages IS NOT NULL
            AND jsonb_typeof(full_messages) = 'array'
            AND jsonb_array_length(full_messages) > 0
            THEN 'chat'
       ELSE 'unknown'
   END
 WHERE request_kind IS NULL;

CREATE INDEX IF NOT EXISTS ledger_request_kind_idx
    ON ledger (request_kind);

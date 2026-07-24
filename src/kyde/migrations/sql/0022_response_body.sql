-- Store the upstream response body verbatim alongside its hash.
--
-- Until now the ledger kept only `output_hash` (SHA-256 of the canonical
-- response JSON) — the request side was preserved via `full_messages`, but
-- the model's reply itself was irrecoverable. This column stores the exact
-- dict that `ledger.append()` hashes, so for every new row:
--
--     sha256(canonical_json(response_body)) == output_hash
--
-- i.e. the stored body is independently verifiable against the signed chain.
--
-- Deliberately NOT part of the signable payload (see ledger._signable) —
-- the byte-level signing contract is locked; this is enrichment alongside
-- the chain, same as full_messages.
--
-- Nullable with no default: NULL means "row predates this migration, body
-- irrecoverable" — distinct from '{}' (a recorded-empty body).

ALTER TABLE ledger ADD COLUMN IF NOT EXISTS response_body JSONB;

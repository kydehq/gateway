-- Host resolution cache. One row per IP that has been resolved (or
-- attempted) — `hostname` IS NULL is a valid cached state meaning
-- "DNS returned nothing", kept so we don't retry every read.
--
-- Two sources: 'admin' (explicit label set via Settings) and 'dns' (lazy
-- reverse-DNS lookup). Admin rows are never overwritten by DNS refresh.
-- TTL only applies to dns rows.
CREATE TABLE IF NOT EXISTS host_resolutions (
    ip            TEXT PRIMARY KEY,
    hostname      TEXT,
    source        TEXT NOT NULL CHECK (source IN ('admin', 'dns')),
    resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_seconds   INT NOT NULL
);

-- Reverse lookup: "which IPs map to this hostname?" — partial index to
-- skip the NULL rows, since DNS misses are by far the majority and we
-- only ever query the index by a non-null hostname.
CREATE INDEX IF NOT EXISTS host_resolutions_hostname_idx
    ON host_resolutions (hostname)
    WHERE hostname IS NOT NULL;

-- Backfill dlp_alerts.severity from the per-finding severity rolled up to
-- MAX(CRITICAL > HIGH > MEDIUM > LOW). Existing rows all read 'medium'
-- because upsert_dlp_alert() never wrote the column. Uppercase output to
-- match what the YAML rules carry and what the frontend's getSeverity
-- helper expects.
UPDATE dlp_alerts SET severity = COALESCE((
    SELECT CASE
        WHEN bool_or(upper(f->>'severity') = 'CRITICAL') THEN 'CRITICAL'
        WHEN bool_or(upper(f->>'severity') = 'HIGH')     THEN 'HIGH'
        WHEN bool_or(upper(f->>'severity') = 'MEDIUM')   THEN 'MEDIUM'
        WHEN bool_or(upper(f->>'severity') = 'LOW')      THEN 'LOW'
        ELSE NULL
      END
      FROM jsonb_array_elements(findings) AS f
), 'MEDIUM');

ALTER TABLE dlp_alerts ALTER COLUMN severity SET DEFAULT 'MEDIUM';

"""Tests for the config loader (kyde.config) and the SQL migration runner
(kyde.migrations) — the deploy-time plumbing that only misfires in the field.
"""

from __future__ import annotations

import pytest

from kyde import config, ledger, migrations


# ---------------------------------------------------------------------------
# config._config_path
# ---------------------------------------------------------------------------


def test_config_path_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KYDE_CONFIG", str(tmp_path / "custom.yaml"))
    assert config._config_path() == tmp_path / "custom.yaml"


def test_config_path_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("KYDE_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config._config_path() == tmp_path / "config.yaml"


# ---------------------------------------------------------------------------
# config.load_upstreams
# ---------------------------------------------------------------------------


def test_load_upstreams_defaults_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("KYDE_CONFIG", str(tmp_path / "nope.yaml"))
    upstreams = config.load_upstreams()
    assert upstreams["openai"]["base"] == "https://api.openai.com"
    assert "anthropic" in upstreams


def test_load_upstreams_merges_overrides_and_new_entries(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
upstreams:
  openai:
    base: http://openai-proxy.internal/
    api_prefix: /v1
  ollama:
    base: http://localhost:11434
  broken:
    api_prefix: /v1
"""
    )
    monkeypatch.setenv("KYDE_CONFIG", str(cfg))
    upstreams = config.load_upstreams()
    # Override wins and trailing slash is stripped.
    assert upstreams["openai"]["base"] == "http://openai-proxy.internal"
    # New upstream extends the registry (api_prefix defaults to "").
    assert upstreams["ollama"] == {
        "base": "http://localhost:11434",
        "api_prefix": "",
    }
    # Entry without 'base' is skipped, defaults survive.
    assert "broken" not in upstreams
    assert "anthropic" in upstreams


def test_load_upstreams_tolerates_unreadable_yaml(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("upstreams: [not: {valid")
    monkeypatch.setenv("KYDE_CONFIG", str(cfg))
    upstreams = config.load_upstreams()
    assert upstreams["openai"]["base"] == "https://api.openai.com"


def test_load_upstreams_ignores_non_dict_upstreams(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("upstreams: [a, b]\n")
    monkeypatch.setenv("KYDE_CONFIG", str(cfg))
    upstreams = config.load_upstreams()
    assert set(upstreams) == {"openai", "anthropic", "gemini", "copilot"}


# ---------------------------------------------------------------------------
# migrations._discover
# ---------------------------------------------------------------------------


def test_discover_raises_when_sql_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(migrations, "_SQL_DIR", tmp_path / "gone")
    with pytest.raises(RuntimeError, match="not found"):
        migrations._discover()


def test_discover_rejects_misnamed_sql_file(monkeypatch, tmp_path):
    (tmp_path / "bad-name.sql").write_text("SELECT 1")
    monkeypatch.setattr(migrations, "_SQL_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="does not match"):
        migrations._discover()


def test_discover_skips_non_sql_files(monkeypatch, tmp_path):
    (tmp_path / "0001_ok.sql").write_text("SELECT 1")
    (tmp_path / "README.md").write_text("docs")
    monkeypatch.setattr(migrations, "_SQL_DIR", tmp_path)
    assert [v for v, _ in migrations._discover()] == ["0001_ok"]


# ---------------------------------------------------------------------------
# migrations.run
# ---------------------------------------------------------------------------


def test_run_raises_on_empty_sql_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(migrations, "_SQL_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="no migration files"):
        migrations.run(ledger._get_pool())


def test_run_applies_pending_migration_once(monkeypatch, tmp_path):
    version = "9999_test_probe"
    (tmp_path / f"{version}.sql").write_text(
        "CREATE TABLE IF NOT EXISTS _kyde_mig_probe (x INT)"
    )
    monkeypatch.setattr(migrations, "_SQL_DIR", tmp_path)
    pool = ledger._get_pool()

    try:
        assert migrations.run(pool) == [version]
        # Idempotent: a second run sees the version as applied.
        assert migrations.run(pool) == []
        with ledger._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('_kyde_mig_probe') AS t")
                assert cur.fetchone()["t"] == "_kyde_mig_probe"
    finally:
        with ledger._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM schema_migrations WHERE version = %s", (version,)
                )
                cur.execute("DROP TABLE IF EXISTS _kyde_mig_probe")
            conn.commit()

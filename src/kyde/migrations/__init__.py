"""
Forward-only SQL migration runner.

Each migration is a single `.sql` file under `migrations/sql/`, named
`NNNN_snake_case.sql` where NNNN is a zero-padded sequence number. Files are
executed in lexicographic order, each inside its own transaction, and the
applied version is recorded in `schema_migrations`.

Chosen over Alembic so the same files can be consumed by the future Rust
proxy port via `sqlx migrate` / `refinery` without translation.
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_SQL_DIR = Path(__file__).parent / "sql"
_FILENAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_CREATE_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _discover() -> list[tuple[str, Path]]:
    """Return [(version, path)] for every migration file, sorted lexically.

    Raises if the sql/ directory is missing entirely — that means the
    package was installed without its data files (see pyproject.toml's
    `[tool.setuptools.package-data]`) and continuing would silently leave
    the DB stuck on the pre-runner schema.
    """
    if not _SQL_DIR.exists():
        raise RuntimeError(
            f"migrations/sql directory not found at {_SQL_DIR}. "
            "This usually means the package was installed without its "
            "package-data: confirm [tool.setuptools.package-data] in "
            "pyproject.toml includes 'migrations/sql/*.sql' and rebuild."
        )
    out: list[tuple[str, Path]] = []
    for path in sorted(_SQL_DIR.iterdir()):
        m = _FILENAME_RE.match(path.name)
        if not m:
            if path.suffix == ".sql":
                raise RuntimeError(
                    f"migration filename {path.name!r} does not match "
                    f"NNNN_snake_case.sql"
                )
            continue
        out.append((path.stem, path))
    return out


def run(pool: "ConnectionPool") -> list[str]:
    """Apply pending migrations. Returns list of versions just applied."""
    migrations = _discover()
    if not migrations:
        # Empty sql/ folder is also a smell — at minimum, 0001_baseline.sql
        # should exist. Fail loud rather than continue against an unknown
        # schema state.
        raise RuntimeError(
            f"no migration files found in {_SQL_DIR}. At least "
            "0001_baseline.sql is expected to ship with the package."
        )

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TRACKING)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row["version"] for row in cur.fetchall()}

    just_applied: list[str] = []
    for version, path in migrations:
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        log.info("applying migration %s", version)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
            conn.commit()
        just_applied.append(version)
    return just_applied

"""PostgreSQL + pgvector layer.

Owns the asyncpg connection pool, applies migrations idempotently on startup,
and exposes the data-access primitives used by IngestService and the MCP tools.

Single backing store for changelog_entries, specs, spec_sections, news,
openqa_tests, deps, manifest. Replaces both the Qdrant layer of changelog-poc
and the SQLite layer of rpm-spec-assistant.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import structlog
from pgvector.asyncpg import register_vector

from .config import settings
from .errors import DBError
from .models import BugReference, ChangelogEntry, NewsItem, OpenQATest, SpecSection

_logger = structlog.get_logger("rpm-mcp.db")

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
PKG_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid namespace OID

# Why: _fetch_text_search concatenates WHERE clauses verbatim, so every clause
# string MUST match this whitelist. Form: `qualified.col OP $N` where OP is
# one of the safe operators. RHS is *always* a placeholder; any literal value
# must be passed via params. Reject anything else to make accidental SQL
# injection (or a footgun on a future caller) impossible.
_SAFE_WHERE_CLAUSE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*"
    r"\s*(=|ILIKE|~\*|>=|<=|<|>|@@)\s*"
    r"\$\d{1,3}$"
)

# Why: keep in sync with migration 005's DEFAULT. When EMBEDDING_MODEL is unset
# we fall back to the same name fastembed picks, so query-time filtering matches
# the values written at ingest time.
_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


def active_embedding_model() -> str:
    return settings.embedding_model or _DEFAULT_EMBEDDING_MODEL


def _validate_where_clauses(clauses: Iterable[str]) -> None:
    for c in clauses:
        if not _SAFE_WHERE_CLAUSE.match(c):
            raise ValueError(f"unsafe WHERE clause rejected: {c!r}")


def content_uuid(package_name: str, content: str) -> uuid.UUID:
    """Deterministic v5 UUID per (package, content) pair — stable dedup key."""
    return uuid.uuid5(PKG_NAMESPACE, f"{package_name}::{content}")


class Database:
    """Async wrapper around an asyncpg pool, with pgvector codec registration."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or settings.database_url
        self._pool: asyncpg.Pool | None = None

    async def connect(self, *, retries: int = 5, backoff: float = 2.0) -> None:
        """Connect to Postgres, retrying on transient connection errors.

        Retries up to *retries* times with exponential backoff (cap 30s).
        Allows the MCP server to start before Postgres is fully ready.
        """
        if self._pool is not None:
            return
        import asyncio

        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                # Bootstrap: ensure pgvector extension exists BEFORE the pool's
                # init callback tries to register the vector codec on each conn.
                bootstrap = await asyncpg.connect(self._dsn)
                try:
                    await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                finally:
                    await bootstrap.close()
                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=settings.pg_pool_min_size,
                    max_size=settings.pg_pool_max_size,
                    init=self._init_conn,
                )
                await self.apply_migrations()
                _logger.info("db_connected", dsn=self._scrubbed_dsn(), attempt=attempt)
                return
            except (OSError, asyncpg.PostgresConnectionError) as exc:
                last_exc = exc
                wait = min(backoff**attempt, 30.0)
                _logger.warning(
                    "db_connect_retry",
                    attempt=attempt,
                    retries=retries,
                    wait_s=round(wait, 1),
                    error=str(exc),
                )
                if attempt < retries:
                    await asyncio.sleep(wait)
        raise DBError(f"Could not connect to Postgres after {retries} attempts: {last_exc}")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _init_conn(conn: asyncpg.Connection) -> None:
        # Must run on every new connection from the pool, not just at startup.
        # pgvector stores vectors as binary; without the codec the driver returns
        # raw bytes instead of lists of floats, silently breaking semantic search.
        await register_vector(conn)

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise DBError("Database.connect() not called -- is PostgreSQL running?")
        return self._pool

    def _scrubbed_dsn(self) -> str:
        # Hide password in logs.
        from urllib.parse import urlparse, urlunparse

        try:
            p = urlparse(self._dsn)
            if p.password:
                netloc = p.netloc.replace(f":{p.password}@", ":***@")
                return urlunparse(p._replace(netloc=netloc))
        except (AttributeError, ValueError) as e:
            _logger.debug("dsn_scrub_failed", error=str(e))
        return self._dsn

    async def apply_migrations(self) -> None:
        """Apply unapplied .sql files from migrations/ in lexicographic order.

        Tracks applied versions in ``schema_migrations(version, applied_at)``.
        Each file is applied at most once; re-running is safe (idempotent bootstrap).
        The tracking table itself is created inline here to avoid a bootstrap paradox.
        """
        if not MIGRATIONS_DIR.exists():
            _logger.warning("no_migrations_dir", path=str(MIGRATIONS_DIR))
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version     TEXT PRIMARY KEY,
                    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            applied: set[str] = {
                r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")
            }
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.name in applied:
                    _logger.debug("migration_skipped", file=f.name)
                    continue
                _logger.info("applying_migration", file=f.name)
                await conn.execute(f.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING",
                    f.name,
                )

    # ------------------------------------------------------------------
    # packages
    # ------------------------------------------------------------------
    async def upsert_package(
        self,
        name: str,
        distro: str = "opensuse",
        latest_version: str | None = None,
        upstream_url: str | None = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO packages (name, distro, latest_version, upstream_url)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (name, distro) DO UPDATE SET
                    latest_version = COALESCE(EXCLUDED.latest_version, packages.latest_version),
                    upstream_url   = COALESCE(EXCLUDED.upstream_url, packages.upstream_url)
                RETURNING id
                """,
                name,
                distro,
                latest_version,
                upstream_url,
            )
            return int(row["id"])

    @staticmethod
    async def _batch_upsert_packages(
        conn: asyncpg.Connection,
        names: list[str],
        distro: str = "opensuse",
    ) -> dict[str, int]:
        """Bulk upsert package rows in one round-trip; return ``{name: id}``.

        Used by upsert_news / upsert_openqa / upsert_testcatalog_bugs to avoid
        an N+1 ``upsert_package`` call per row.
        """
        if not names:
            return {}
        rows = await conn.fetch(
            """
            INSERT INTO packages (name, distro)
            SELECT name, $2 FROM unnest($1::text[]) AS t(name)
            ON CONFLICT (name, distro) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, name
            """,
            names,
            distro,
        )
        return {str(r["name"]): int(r["id"]) for r in rows}

    async def get_package_id(self, name: str, distro: str = "opensuse") -> int | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM packages WHERE name = $1 AND distro = $2",
                name,
                distro,
            )
            return int(row["id"]) if row else None

    async def get_upstream_url(self, name: str, distro: str = "opensuse") -> str | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT upstream_url FROM packages WHERE name = $1 AND distro = $2",
                name,
                distro,
            )
            url = row.get("upstream_url") if row else None
            return str(url) if url else None

    # ------------------------------------------------------------------
    # changelog_entries
    # ------------------------------------------------------------------
    async def upsert_changelog_entries(
        self,
        package_name: str,
        package_id: int,
        entries: Iterable[ChangelogEntry],
        embeddings: list[list[float]],
        source_name: str,
    ) -> int:
        """Bulk upsert. Returns inserted count (conflicts skipped)."""
        active_model = active_embedding_model()
        rows: list[tuple[Any, ...]] = []
        for e, emb in zip(entries, embeddings, strict=True):
            rows.append(
                (
                    content_uuid(package_name, e.content),
                    package_id,
                    e.version,
                    e.author,
                    e.date,
                    e.content,
                    source_name,
                    emb or None,
                    active_model,
                )
            )
        if not rows:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO changelog_entries
                    (id, package_id, version, author, entry_date, content,
                     source_name, embedding, embedding_model)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (id) DO NOTHING
                """,
                rows,
            )
        return len(rows)

    async def count_entries(self, package_id: int) -> int:
        """Cheap row-count for *package_id*, using the package_id index.

        Used by the stale-fallback path which only needs existence + count,
        not the row data.
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM changelog_entries WHERE package_id = $1",
                package_id,
            ) or 0

    async def fetch_entries(self, package_id: int, limit: int | None = None) -> list[asyncpg.Record]:
        q = """
            SELECT version, author, entry_date, content
            FROM changelog_entries
            WHERE package_id = $1
            ORDER BY entry_date DESC NULLS LAST, version DESC
        """
        async with self.pool.acquire() as conn:
            if limit is not None:
                return await conn.fetch(q + " LIMIT $2", package_id, limit)
            return await conn.fetch(q, package_id)

    async def fetch_entries_in_range(
        self, package_id: int, since: datetime, until: datetime
    ) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT version, author, entry_date, content
                FROM changelog_entries
                WHERE package_id = $1
                  AND entry_date BETWEEN $2 AND $3
                ORDER BY entry_date DESC
                """,
                package_id,
                since,
                until,
            )

    # ------------------------------------------------------------------
    # semantic + FTS search
    # ------------------------------------------------------------------
    async def semantic_search(self, embedding: list[float], limit: int = 10) -> list[asyncpg.Record]:
        # Why: cosine distance is only meaningful between vectors from the same
        # model. Mixing models silently corrupts ranking; filter rows to the
        # currently active model (migration 005 columns row-by-row).
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT p.name AS package, ce.version, ce.entry_date, ce.content,
                       ce.embedding <=> $1::vector AS distance
                FROM changelog_entries ce
                JOIN packages p ON p.id = ce.package_id
                WHERE ce.embedding IS NOT NULL
                  AND ce.embedding_model = $3
                ORDER BY ce.embedding <=> $1::vector
                LIMIT $2
                """,
                embedding,
                limit,
                active_embedding_model(),
            )

    async def fts_search(
        self, query: str, limit: int = 10, since: datetime | None = None
    ) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT p.name AS package, ce.version, ce.entry_date, ce.content,
                       ts_rank(ce.tsv, plainto_tsquery('english', $1)) AS rank
                FROM changelog_entries ce
                JOIN packages p ON p.id = ce.package_id
                WHERE ce.tsv @@ plainto_tsquery('english', $1)
                  AND ($3::timestamptz IS NULL OR ce.entry_date >= $3)
                ORDER BY rank DESC
                LIMIT $2
                """,
                query,
                limit,
                since,
            )

    async def _fetch_text_search(
        self,
        *,
        where_clauses: list[str],
        params: list[Any],
        include_package: bool = False,
        limit: int | None = None,
    ) -> list[asyncpg.Record]:
        # Why: caller owns $N placeholder numbering — clauses are concatenated verbatim
        # so each builder can keep its SQL readable without a renumbering pass here.
        # Every clause is validated against _SAFE_WHERE_CLAUSE to block any future
        # caller from sneaking in user-controlled SQL fragments.
        _validate_where_clauses(where_clauses)
        select_cols = (
            "p.name AS package, ce.version, ce.entry_date, ce.content"
            if include_package
            else "ce.version, ce.entry_date, ce.content"
        )
        sql = (
            f"SELECT {select_cols}\n"
            "FROM changelog_entries ce\n"
            "JOIN packages p ON p.id = ce.package_id\n"
            f"WHERE {' AND '.join(where_clauses)}\n"
            "ORDER BY ce.entry_date DESC"
        )
        if limit is not None:
            sql += f"\nLIMIT {int(limit)}"
        async with self.pool.acquire() as conn:
            return await conn.fetch(sql, *params)

    async def list_package_bugs(
        self, package_name: str, since: datetime | None = None
    ) -> list[asyncpg.Record]:
        """Return entries for *package_name* mentioning a bsc#/boo#/bnc# bug reference."""
        where = ["p.name = $1", "ce.content ~* $2"]
        params: list[Any] = [package_name, r"\m(bsc|boo|bnc)#\d+"]
        if since is not None:
            where.append("ce.entry_date >= $3")
            params.append(since)
        return await self._fetch_text_search(where_clauses=where, params=params)

    async def _substring_search(
        self, needle: str, package_name: str | None, limit: int = 200
    ) -> list[asyncpg.Record]:
        """Case-insensitive ILIKE '%needle%' over changelog_entries.content.

        Scoped to *package_name* if provided; otherwise global with *limit* cap.
        """
        like = f"%{needle}%"
        if package_name:
            return await self._fetch_text_search(
                where_clauses=["p.name = $1", "ce.content ILIKE $2"],
                params=[package_name, like],
                include_package=True,
            )
        return await self._fetch_text_search(
            where_clauses=["ce.content ILIKE $1"],
            params=[like],
            include_package=True,
            limit=limit,
        )

    async def find_bug(self, bug_ref: str, package_name: str | None = None) -> list[asyncpg.Record]:
        """Case-insensitive substring search for a specific bug ID (e.g. bsc#1234567)."""
        return await self._substring_search(bug_ref, package_name)

    async def list_package_cves(
        self, package_name: str, since: datetime | None = None
    ) -> list[asyncpg.Record]:
        """Return all changelog entries for *package_name* that mention a CVE ID."""
        where = ["p.name = $1", "ce.content ILIKE $2"]
        params: list[Any] = [package_name, "%CVE-%"]
        if since is not None:
            where.append("ce.entry_date >= $3")
            params.append(since)
        return await self._fetch_text_search(where_clauses=where, params=params)

    async def find_cve(self, cve_id: str, package_name: str | None = None) -> list[asyncpg.Record]:
        """Case-insensitive substring search for a specific CVE ID."""
        return await self._substring_search(cve_id, package_name)

    # ------------------------------------------------------------------
    # specs + spec_sections
    # ------------------------------------------------------------------
    async def upsert_spec(self, package_id: int, source: str, version: str | None, content: str) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO specs (package_id, source, version, content)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (package_id, source) DO UPDATE SET
                    version = EXCLUDED.version,
                    content = EXCLUDED.content,
                    last_updated = now()
                RETURNING id
                """,
                package_id,
                source,
                version,
                content,
            )
            return int(row["id"])

    async def replace_spec_sections(
        self,
        spec_id: int,
        sections: list[SpecSection],
        embeddings: list[list[float]],
    ) -> None:
        active_model = active_embedding_model()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM spec_sections WHERE spec_id = $1", spec_id)
            rows = [
                (spec_id, s.section_name, s.chunk_index, s.content, emb or None, active_model)
                for s, emb in zip(sections, embeddings, strict=True)
            ]
            if rows:
                await conn.executemany(
                    """
                        INSERT INTO spec_sections
                            (spec_id, section_name, chunk_index, content, embedding, embedding_model)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                    rows,
                )

    async def get_spec(self, package_id: int, source: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT id, content, version FROM specs WHERE package_id = $1 AND source = $2",
                package_id,
                source,
            )

    # ------------------------------------------------------------------
    # news + openqa
    # ------------------------------------------------------------------
    async def upsert_news(self, items: Iterable[NewsItem]) -> int:
        """Bulk insert news rows. Implicitly upserts referenced packages in
        one round-trip (the package_name -> id resolution would otherwise be
        N+1 against ``packages``).
        """
        items_list = list(items)
        if not items_list:
            return 0

        names = sorted({n.package_name for n in items_list if n.package_name})
        async with self.pool.acquire() as conn, conn.transaction():
            id_by_name = await self._batch_upsert_packages(conn, names)

            rows = [
                (
                    id_by_name.get(n.package_name) if n.package_name else None,
                    n.title,
                    n.source,
                    n.item_type,
                    n.importance,
                    n.content,
                    n.url,
                    n.date,
                )
                for n in items_list
            ]
            await conn.executemany(
                """
                INSERT INTO news
                    (package_id, title, source, item_type, importance, content, url, item_date)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (title, source) DO NOTHING
                """,
                rows,
            )
        return len(rows)

    async def get_news(self, package_name: str | None = None, limit: int = 10) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT n.title, n.source, n.item_type, n.importance, n.content, n.url, n.item_date
                FROM news n
                LEFT JOIN packages p ON p.id = n.package_id
                WHERE ($1::text IS NULL OR p.name = $1)
                ORDER BY n.item_date DESC LIMIT $2
                """,
                package_name,
                limit,
            )

    async def upsert_openqa(
        self,
        tests: Iterable[OpenQATest],
        source: str = "openqa",
    ) -> int:
        """Bulk upsert openQA/TestCatalog test rows.

        *source* distinguishes data origin: ``'openqa'`` (local .pm scan) vs
        ``'testcatalog'`` (live API). Implicitly upserts every referenced
        package in a single round-trip rather than one-per-row.
        """
        tests_list = list(tests)
        if not tests_list:
            return 0
        async with self.pool.acquire() as conn, conn.transaction():
            id_by_name = await self._batch_upsert_packages(
                conn, sorted({t.package_name for t in tests_list})
            )
            rows = [
                (id_by_name[t.package_name], t.test_path, t.summary, source)
                for t in tests_list
            ]
            await conn.executemany(
                """
                INSERT INTO openqa_tests (package_id, test_path, summary, source)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (package_id, test_path, source) DO UPDATE SET
                    summary = EXCLUDED.summary
                """,
                rows,
            )
        return len(rows)

    async def get_openqa_tests(
        self,
        package_name: str,
        source: str | None = None,
    ) -> list[asyncpg.Record]:
        """Return test rows for *package_name*.

        When *source* is given (e.g. ``'openqa'`` or ``'testcatalog'``), only
        rows from that source are returned. Pass ``None`` to get all sources.
        """
        async with self.pool.acquire() as conn:
            if source is None:
                return await conn.fetch(
                    """
                    SELECT t.test_path, t.summary, t.source
                    FROM openqa_tests t
                    JOIN packages p ON p.id = t.package_id
                    WHERE p.name = $1
                    ORDER BY t.source, t.test_path
                    """,
                    package_name,
                )
            return await conn.fetch(
                """
                SELECT t.test_path, t.summary, t.source
                FROM openqa_tests t
                JOIN packages p ON p.id = t.package_id
                WHERE p.name = $1 AND t.source = $2
                ORDER BY t.test_path
                """,
                package_name,
                source,
            )

    async def upsert_testcatalog_bugs(self, bugs: Iterable[BugReference]) -> int:
        """Bulk upsert Bugzilla bugs fetched from the TestCatalog analytics API.

        Implicitly batch-upserts every referenced package in a single round
        trip so per-row N+1 is avoided.
        """
        bugs_list = list(bugs)
        if not bugs_list:
            return 0
        async with self.pool.acquire() as conn, conn.transaction():
            id_by_name = await self._batch_upsert_packages(
                conn, sorted({b.package_name for b in bugs_list})
            )
            rows = [
                (
                    id_by_name[b.package_name],
                    b.bug_id,
                    b.summary,
                    b.status,
                    b.severity,
                    b.component,
                    b.assigned_to,
                    b.resolution,
                )
                for b in bugs_list
            ]
            await conn.executemany(
                """
                INSERT INTO testcatalog_bugs
                    (package_id, bug_id, summary, status, severity, component, assigned_to, resolution)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (package_id, bug_id) DO UPDATE SET
                    summary     = EXCLUDED.summary,
                    status      = EXCLUDED.status,
                    severity    = EXCLUDED.severity,
                    component   = EXCLUDED.component,
                    assigned_to = EXCLUDED.assigned_to,
                    resolution  = EXCLUDED.resolution
                """,
                rows,
            )
        return len(rows)

    async def get_testcatalog_bugs(self, package_name: str) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT b.bug_id, b.summary, b.status, b.severity,
                       b.component, b.assigned_to, b.resolution
                FROM testcatalog_bugs b
                JOIN packages p ON p.id = b.package_id
                WHERE p.name = $1
                ORDER BY b.bug_id DESC
                """,
                package_name,
            )

    async def find_untested_packages(
        self,
        days: int = 90,
        limit: int = 5,
        cli_only: bool = False,
    ) -> list[asyncpg.Record]:
        """Packages with recent changelog entries but no openqa_tests rows.

        With ``cli_only=True``, restrict to entries that announce a new CLI
        flag or option (tsvector prefilter + regex confirmation: action verb
        must precede ``--flag`` or an ``option|flag|parameter|argument|switch``
        keyword). Result rows include a ``sample`` excerpt in that mode.
        """
        cli_regex = (
            r"(?:^|[[:space:]])"
            r"(?:new|add(?:ed|s)?|introduc(?:e|ed|es)|support(?:s|ed)?)"
            r"[[:space:]][^.\n]{0,60}?"
            r"(?:--[a-zA-Z][a-zA-Z0-9_-]+"
            r"|(?:command[[:space:]-]line|cli)[[:space:]](?:option|flag|parameter|argument|switch)"
            r"|(?:option|flag|parameter|argument|switch)[[:space:]]+\"?--[a-zA-Z])"
        )
        cli_filter = (
            """
                  AND ce.tsv @@ to_tsquery(
                      'english',
                      '(new | add | introduc:* | support:*) & '
                      '(option | flag | parameter | argument | switch)'
                  )
                  AND ce.content ~* $3
            """
            if cli_only
            else ""
        )
        sample_select = (
            ", (array_agg(ce.content ORDER BY ce.entry_date DESC))[1] AS sample"
            if cli_only
            else ", NULL::text AS sample"
        )
        sql = f"""
            WITH hits AS (
                SELECT ce.package_id,
                       MAX(ce.entry_date)            AS latest_change,
                       COUNT(*)                      AS change_count
                       {sample_select}
                FROM changelog_entries ce
                WHERE ce.entry_date >= now() - make_interval(days => $1)
                {cli_filter}
                GROUP BY ce.package_id
            )
            SELECT p.name,
                   p.distro,
                   h.latest_change,
                   h.change_count,
                   h.sample
            FROM hits h
            JOIN packages p ON p.id = h.package_id
            WHERE NOT EXISTS (
                SELECT 1 FROM openqa_tests t WHERE t.package_id = p.id
            )
            ORDER BY h.latest_change DESC
            LIMIT $2
            """
        async with self.pool.acquire() as conn:
            if cli_only:
                return await conn.fetch(sql, days, limit, cli_regex)
            return await conn.fetch(sql, days, limit)

    async def compare_versions(self, package: str) -> list[asyncpg.Record]:
        """Latest changelog version per distro for *package*."""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT p.distro,
                       ce.version,
                       ce.entry_date
                FROM packages p
                JOIN changelog_entries ce ON ce.package_id = p.id
                WHERE p.name = $1
                  AND ce.entry_date = (
                      SELECT MAX(ce2.entry_date)
                      FROM changelog_entries ce2
                      WHERE ce2.package_id = p.id
                  )
                ORDER BY p.distro
                """,
                package,
            )

    # ------------------------------------------------------------------
    # deps
    # ------------------------------------------------------------------
    async def replace_deps(self, package_id: int, dep_names: Iterable[str], kind: str) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM deps WHERE package_id = $1 AND kind = $2",
                package_id,
                kind,
            )
            rows = [(package_id, d, kind) for d in dep_names]
            if rows:
                await conn.executemany(
                    "INSERT INTO deps (package_id, dep_name, kind) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    rows,
                )

    async def get_deps(self, package_name: str, kind: str = "requires") -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.dep_name
                FROM deps d
                JOIN packages p ON p.id = d.package_id
                WHERE p.name = $1 AND d.kind = $2
                ORDER BY d.dep_name
                """,
                package_name,
                kind,
            )
        return [r["dep_name"] for r in rows]

    async def get_reverse_deps(self, package_name: str) -> list[str]:
        """Return packages that 'requires' anything matching package_name."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT p.name
                FROM deps d
                JOIN packages p ON p.id = d.package_id
                WHERE d.kind = 'requires' AND d.dep_name = $1
                ORDER BY p.name
                """,
                package_name,
            )
        return [r["name"] for r in rows]

    # ------------------------------------------------------------------
    # manifest / eviction
    # ------------------------------------------------------------------
    async def touch_manifest(self, package_id: int, kind: str = "changelog") -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO manifest (package_id, kind, synced_at)
                VALUES ($1, $2, now())
                ON CONFLICT (package_id, kind) DO UPDATE SET synced_at = now()
                """,
                package_id,
                kind,
            )

    async def is_fresh(self, package_id: int, ttl_seconds: int, kind: str = "changelog") -> bool:
        """True if the manifest row exists and is younger than *ttl_seconds*.

        Returns False both when the entry is stale and when no manifest row
        exists at all -- callers treat "never ingested" as "not fresh", which
        triggers the same ingest path.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT synced_at FROM manifest WHERE package_id = $1 AND kind = $2",
                package_id,
                kind,
            )
        if not row:
            return False
        age = (datetime.now(UTC) - row["synced_at"]).total_seconds()
        return age < ttl_seconds

    async def get_synced_at(self, package_id: int, kind: str = "changelog") -> datetime | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT synced_at FROM manifest WHERE package_id = $1 AND kind = $2",
                package_id,
                kind,
            )
        return row["synced_at"] if row else None

    async def evict_stale(self, ttl_seconds: int, kind: str = "changelog") -> list[str]:
        """Delete cached rows for packages whose manifest *kind* is older than
        TTL. Only ``kind='changelog'`` deletes from ``changelog_entries``; other
        kinds only purge their manifest entry (specs evict via the worker's
        respec flow). Returns evicted package names.

        Single CTE with ``FOR UPDATE SKIP LOCKED`` so a concurrent
        ``touch_manifest`` from ``IngestService`` blocks (or, if it already
        holds the row lock, eviction simply skips that package this pass).
        Closes the SELECT-then-DELETE race that the prior two-statement
        version had under READ COMMITTED.
        """
        # Each kind targets a different content table; the table name MUST
        # come from this static whitelist (never user input) so the f-string
        # interpolation below is safe.
        content_table = {"changelog": "changelog_entries", "spec": "specs"}.get(kind)
        content_cte = ""
        if content_table is not None:
            content_cte = (
                f", _del_content AS (DELETE FROM {content_table} "  # noqa: S608
                "WHERE package_id IN (SELECT package_id FROM stale))"
            )
        sql = (
            "WITH stale AS MATERIALIZED ("  # noqa: S608
            "  SELECT package_id FROM manifest"
            "  WHERE kind = $2"
            "    AND synced_at < now() - make_interval(secs => $1)"
            "  FOR UPDATE SKIP LOCKED"
            "),"
            "_del_manifest AS ("
            "  DELETE FROM manifest"
            "  WHERE kind = $2"
            "    AND package_id IN (SELECT package_id FROM stale)"
            ")" + content_cte + " "
            "SELECT p.name FROM packages p "
            "WHERE p.id IN (SELECT package_id FROM stale)"
        )
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(sql, ttl_seconds, kind)
        return [r["name"] for r in rows]

    async def get_sync_ages(
        self,
        package_names: list[str] | None = None,
        kind: str = "changelog",
    ) -> list[dict[str, Any]]:
        """Return [{name, synced_at, age_seconds}] for packages with a manifest
        row of the given *kind*.

        If *package_names* is given, restrict to those names (unsynced ones are
        omitted). If None, return all rows of that kind.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.name, m.synced_at,
                       EXTRACT(EPOCH FROM (now() - m.synced_at))::int AS age_seconds
                FROM manifest m
                JOIN packages p ON p.id = m.package_id
                WHERE m.kind = $2
                  AND ($1::text[] IS NULL OR p.name = ANY($1::text[]))
                ORDER BY m.synced_at ASC
                """,
                package_names,
                kind,
            )
        return [dict(r) for r in rows]

    async def news_age_seconds(self) -> int | None:
        """Seconds since the most recently ingested news item. ``None`` if
        the ``news`` table is empty. Drives the worker's news-TTL guard.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT EXTRACT(EPOCH FROM (now() - MAX(item_date)))::int AS age FROM news"
            )
        return row["age"] if row and row["age"] is not None else None

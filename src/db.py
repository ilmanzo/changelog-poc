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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asyncpg
import structlog
from pgvector.asyncpg import register_vector

from .config import settings
from .models import ChangelogEntry, NewsItem, OpenQATest, SpecSection

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

    async def connect(self) -> None:
        if self._pool is not None:
            return
        # Bootstrap: ensure the pgvector extension exists BEFORE the pool's
        # init callback tries to register the vector codec on each new conn.
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
        _logger.info("db_connected", dsn=self._scrubbed_dsn())

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await register_vector(conn)

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() not called")
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
        """Apply every .sql file in migrations/ in lexicographic order."""
        if not MIGRATIONS_DIR.exists():
            _logger.warning("no_migrations_dir", path=str(MIGRATIONS_DIR))
            return
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        async with self.pool.acquire() as conn:
            for f in files:
                _logger.info("applying_migration", file=f.name)
                await conn.execute(f.read_text())

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
                name, distro, latest_version, upstream_url,
            )
            return int(row["id"])

    async def get_package_id(self, name: str, distro: str = "opensuse") -> int | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM packages WHERE name = $1 AND distro = $2",
                name, distro,
            )
            return int(row["id"]) if row else None

    async def get_upstream_url(self, name: str, distro: str = "opensuse") -> str | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT upstream_url FROM packages WHERE name = $1 AND distro = $2",
                name, distro,
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
        rows: list[tuple[Any, ...]] = []
        for e, emb in zip(entries, embeddings):
            rows.append((
                content_uuid(package_name, e.content),
                package_id,
                e.version,
                e.author,
                e.date,
                e.content,
                source_name,
                emb or None,
            ))
        if not rows:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO changelog_entries
                    (id, package_id, version, author, entry_date, content, source_name, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (id) DO NOTHING
                """,
                rows,
            )
        return len(rows)

    async def fetch_entries(
        self, package_id: int, limit: int | None = None
    ) -> list[asyncpg.Record]:
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
                package_id, since, until,
            )

    # ------------------------------------------------------------------
    # semantic + FTS search
    # ------------------------------------------------------------------
    async def semantic_search(
        self, embedding: list[float], limit: int = 10
    ) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT p.name AS package, ce.version, ce.entry_date, ce.content,
                       ce.embedding <=> $1::vector AS distance
                FROM changelog_entries ce
                JOIN packages p ON p.id = ce.package_id
                WHERE ce.embedding IS NOT NULL
                ORDER BY ce.embedding <=> $1::vector
                LIMIT $2
                """,
                embedding, limit,
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
                query, limit, since,
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

    async def find_bug(
        self, bug_ref: str, package_name: str | None = None
    ) -> list[asyncpg.Record]:
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

    async def find_cve(
        self, cve_id: str, package_name: str | None = None
    ) -> list[asyncpg.Record]:
        """Case-insensitive substring search for a specific CVE ID."""
        return await self._substring_search(cve_id, package_name)

    # ------------------------------------------------------------------
    # specs + spec_sections
    # ------------------------------------------------------------------
    async def upsert_spec(
        self, package_id: int, source: str, version: str | None, content: str
    ) -> int:
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
                package_id, source, version, content,
            )
            return int(row["id"])

    async def replace_spec_sections(
        self,
        spec_id: int,
        sections: list[SpecSection],
        embeddings: list[list[float]],
    ) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM spec_sections WHERE spec_id = $1", spec_id)
                rows = [
                    (spec_id, s.section_name, s.chunk_index, s.content, emb or None)
                    for s, emb in zip(sections, embeddings)
                ]
                if rows:
                    await conn.executemany(
                        """
                        INSERT INTO spec_sections
                            (spec_id, section_name, chunk_index, content, embedding)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        rows,
                    )

    async def get_spec(self, package_id: int, source: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT id, content, version FROM specs WHERE package_id = $1 AND source = $2",
                package_id, source,
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
            id_by_name: dict[str, int] = {}
            if names:
                pkg_rows = await conn.fetch(
                    """
                    INSERT INTO packages (name, distro)
                    SELECT name, 'opensuse' FROM unnest($1::text[]) AS t(name)
                    ON CONFLICT (name, distro) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id, name
                    """,
                    names,
                )
                id_by_name = {str(r["name"]): int(r["id"]) for r in pkg_rows}

            rows = [
                (
                    id_by_name.get(n.package_name) if n.package_name else None,
                    n.title, n.source, n.item_type, n.importance,
                    n.content, n.url, n.date,
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

    async def get_news(
        self, package_name: str | None = None, limit: int = 10
    ) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT n.title, n.source, n.item_type, n.importance, n.content, n.url, n.item_date
                FROM news n
                LEFT JOIN packages p ON p.id = n.package_id
                WHERE ($1::text IS NULL OR p.name = $1)
                ORDER BY n.item_date DESC LIMIT $2
                """,
                package_name, limit,
            )

    async def upsert_openqa(self, tests: Iterable[OpenQATest]) -> int:
        rows: list[tuple[Any, ...]] = []
        for t in tests:
            pkg_id = await self.upsert_package(t.package_name)
            rows.append((pkg_id, t.test_path, t.summary))
        if not rows:
            return 0
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO openqa_tests (package_id, test_path, summary)
                VALUES ($1, $2, $3)
                ON CONFLICT (package_id, test_path) DO UPDATE SET
                    summary = EXCLUDED.summary
                """,
                rows,
            )
        return len(rows)

    async def get_openqa_tests(self, package_name: str) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT t.test_path, t.summary
                FROM openqa_tests t
                JOIN packages p ON p.id = t.package_id
                WHERE p.name = $1
                ORDER BY t.test_path
                """,
                package_name,
            )

    async def find_untested_packages(
        self, days: int = 90, limit: int = 5,
    ) -> list[asyncpg.Record]:
        """Packages with recent changelog entries but no openqa_tests rows."""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT DISTINCT p.name,
                       MAX(ce.entry_date) AS latest_change,
                       COUNT(ce.id)       AS change_count
                FROM packages p
                JOIN changelog_entries ce ON ce.package_id = p.id
                LEFT JOIN openqa_tests t  ON t.package_id  = p.id
                WHERE t.id IS NULL
                  AND ce.entry_date >= now() - make_interval(days => $1)
                GROUP BY p.name
                ORDER BY latest_change DESC
                LIMIT $2
                """,
                days, limit,
            )

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
    async def replace_deps(
        self, package_id: int, dep_names: Iterable[str], kind: str
    ) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM deps WHERE package_id = $1 AND kind = $2",
                    package_id, kind,
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
                package_name, kind,
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
    async def touch_manifest(
        self, package_id: int, kind: str = "changelog"
    ) -> None:
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

    async def is_fresh(
        self, package_id: int, ttl_seconds: int, kind: str = "changelog"
    ) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT synced_at FROM manifest WHERE package_id = $1 AND kind = $2",
                package_id,
                kind,
            )
        if not row:
            return False
        age = (datetime.now(timezone.utc) - row["synced_at"]).total_seconds()
        return age < ttl_seconds

    async def get_synced_at(
        self, package_id: int, kind: str = "changelog"
    ) -> datetime | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT synced_at FROM manifest WHERE package_id = $1 AND kind = $2",
                package_id,
                kind,
            )
        return row["synced_at"] if row else None

    async def evict_stale(
        self, ttl_seconds: int, kind: str = "changelog"
    ) -> list[str]:
        """Delete cached rows for packages whose manifest *kind* is older than
        TTL. Only ``kind='changelog'`` deletes from ``changelog_entries``; other
        kinds only purge their manifest entry (specs evict via the worker's
        respec flow). Returns evicted package names.
        """
        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                SELECT p.id, p.name FROM packages p
                JOIN manifest m ON m.package_id = p.id AND m.kind = $2
                WHERE m.synced_at < now() - make_interval(secs => $1)
                """,
                ttl_seconds,
                kind,
            )
            names = [r["name"] for r in rows]
            if names:
                ids = [r["id"] for r in rows]
                if kind == "changelog":
                    await conn.execute(
                        "DELETE FROM changelog_entries WHERE package_id = ANY($1::bigint[])",
                        ids,
                    )
                elif kind == "spec":
                    await conn.execute(
                        "DELETE FROM specs WHERE package_id = ANY($1::bigint[])",
                        ids,
                    )
                await conn.execute(
                    "DELETE FROM manifest WHERE package_id = ANY($1::bigint[]) AND kind = $2",
                    ids,
                    kind,
                )
        return names

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

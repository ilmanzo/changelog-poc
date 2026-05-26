"""Unit tests for src/db.py validators — pure functions, no DB needed."""
from __future__ import annotations

import pytest

from src.db import _SAFE_WHERE_CLAUSE, _validate_where_clauses


@pytest.mark.parametrize(
    "clause",
    [
        "p.name = $1",
        "ce.content ILIKE $2",
        "ce.entry_date >= $3",
        "ce.tsv @@ $1",
        "ce.content ~* $2",
        "ce.entry_date < $99",
        "version = $1",  # unqualified column is fine
        "schema_name.table.col = $1",  # multi-segment qualifier
    ],
)
def test_safe_where_clause_accepts(clause: str) -> None:
    assert _SAFE_WHERE_CLAUSE.match(clause), f"valid clause rejected: {clause!r}"


@pytest.mark.parametrize(
    "clause",
    [
        # SQL injection attempt with a literal RHS
        "p.name = 'foo' OR 1=1; --",
        "ce.content ILIKE '%CVE-%'",  # literal RHS not allowed — must parameterize
        "ce.content ~* '\\m(bsc)#\\d+'",  # literal RHS not allowed — must parameterize
        "1=1",  # no column reference
        "p.name; DROP TABLE packages",  # statement separator
        "p.name = $1 OR 1=1",  # trailing junk
        "p.name = $",  # missing placeholder number
        "p.name = $9999",  # 4 digits — too many
        "p.name LIKE $1",  # LIKE not on whitelist (use ILIKE)
        "p.name IN ($1)",  # IN not on whitelist
        "p.name=$1; SELECT pg_sleep(10)",
        "",
    ],
)
def test_safe_where_clause_rejects(clause: str) -> None:
    assert not _SAFE_WHERE_CLAUSE.match(clause), f"unsafe clause accepted: {clause!r}"


def test_validate_where_clauses_raises_on_unsafe() -> None:
    with pytest.raises(ValueError, match="unsafe WHERE clause"):
        _validate_where_clauses(["p.name = $1", "1=1; DROP TABLE packages"])


def test_validate_where_clauses_passes_on_all_safe() -> None:
    # No exception means OK
    _validate_where_clauses(["p.name = $1", "ce.content ILIKE $2", "ce.entry_date >= $3"])

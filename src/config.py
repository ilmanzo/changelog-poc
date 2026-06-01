"""Application settings loaded from environment variables.

See README for the full table of env vars and defaults.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Tunables for rpm-mcp. Instantiated once as ``settings``."""

    # PostgreSQL
    database_url: str = "postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp"
    pg_pool_min_size: int = 10
    pg_pool_max_size: int = 80

    # Cache / eviction (DD12 — per-kind TTLs)
    cache_ttl_news_s: int = 86400  # 24h: RSS/Bodhi feeds polled daily
    cache_ttl_changelog_s: int = 86400  # 24h: package builds rarely flip more often
    cache_ttl_spec_s: int = 604800  # 7d: spec churn is glacial
    cache_max_entries: int = 1000  # per package fetch cap
    # NB: not a cache-size limit despite the prefix -- it's the per-entry
    # truncation cap applied in src/sanitize.py:scrub_external.
    cache_max_entry_bytes: int = 8192
    eviction_min_interval_s: int = 3600  # min gap between opportunistic sweeps

    # Embedding
    embedding_model: str = ""  # empty -> fastembed default (BAAI/bge-small-en-v1.5)
    embedding_dim: int = 384
    embedding_batch_size: int = 100
    embedding_chunk_size: int = 1000  # spec-section chunk size in chars
    embedding_chunk_overlap: int = 100
    # Why: embed_batch materialises its input into a list before handing
    # off to fastembed; a caller passing a multi-million-item generator
    # would OOM. chunk_text similarly has no built-in cap.
    embedding_max_inputs: int = 10_000  # hard cap per embed_batch call
    embedding_max_chunks: int = 1_000   # hard cap on chunk_text output

    # Logging
    log_format: str = "text"  # "json" for structured prod logs

    # Source registry
    fetch_strategy: str = "waterfall"  # "waterfall" | "parallel"

    # Git repo cache
    git_cache_max_entries: int = 50

    # Composite-tool caps
    f4_max_packages: int = 50  # cap for get_dependency_changes

    # HTTP
    obs_timeout_total: int = 30
    obs_timeout_connect: int = 10
    obs_max_retries: int = 3

    # Subprocess (rpm, git, etc.)
    subprocess_timeout_s: int = 60

    # Worker (centralised ingestion)
    worker_concurrency: int = 10

    # Test repo (F4 — os-autoinst coverage analysis)
    test_repo_url: str = "https://github.com/os-autoinst/os-autoinst-distri-opensuse"
    test_repo_path: str = str(__import__("pathlib").Path.home() / ".cache" / "rpm-mcp" / "os-autoinst")

    # Upstream forge tokens (F3b — optional, anonymous without)
    github_token: str = ""
    gitlab_token: str = ""

    # Tool execution timeouts (DD16; category='fast'|'search'; None = no limit)
    tool_timeout_fast_s: int = 10  # DB-read tools: find_*, list_*, get_*, compare_*
    tool_timeout_search_s: int = 30  # vector/FTS/live-API tools: semantic_search, fts_search, etc.

    # TestCatalog API (read endpoints are public; token only needed for write ops)
    testcatalog_url: str = "http://testcatalog.qa.suse.de:3001"
    testcatalog_api_key: str = ""  # optional Bearer JWT

    # Optional .env file in the working directory; OS env vars still win.
    # `extra="ignore"` so unrelated keys in a shared .env don't error.
    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


settings = Settings()

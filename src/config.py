"""Application settings loaded from environment variables.

See README for the full table of env vars and defaults.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Tunables for rpm-mcp. Instantiated once as ``settings``."""

    # PostgreSQL
    database_url: str = "postgresql://rpm_mcp:rpm_mcp@127.0.0.1:5432/rpm_mcp"
    pg_pool_min_size: int = 2
    pg_pool_max_size: int = 20

    # Cache / eviction (DD12 — per-kind TTLs)
    cache_ttl_news_s: int = 86400         # 24h: RSS/Bodhi feeds polled daily
    cache_ttl_changelog_s: int = 86400    # 24h: package builds rarely flip more often
    cache_ttl_spec_s: int = 604800        # 7d: spec churn is glacial
    cache_max_entries: int = 1000         # per package fetch cap
    # NB: not a cache-size limit despite the prefix -- it's the per-entry
    # truncation cap applied in src/sanitize.py:scrub_external.
    cache_max_entry_bytes: int = 8192
    eviction_min_interval_s: int = 3600   # min gap between opportunistic sweeps

    # Embedding
    embedding_model: str = ""             # empty -> fastembed default (BAAI/bge-small-en-v1.5)
    embedding_dim: int = 384
    embedding_batch_size: int = 100
    embedding_chunk_size: int = 1000      # spec-section chunk size in chars
    embedding_chunk_overlap: int = 100

    # Logging
    log_format: str = "text"              # "json" for structured prod logs

    # Source registry
    fetch_strategy: str = "waterfall"     # "waterfall" | "parallel"

    # Git repo cache
    git_cache_max_entries: int = 50

    # Composite-tool caps
    f4_max_packages: int = 50             # cap for get_dependency_changes

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

    model_config = {"env_prefix": ""}


settings = Settings()

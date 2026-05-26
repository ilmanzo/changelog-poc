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

    # Cache / eviction
    cache_ttl_seconds: int = 604800       # 1 week
    cache_max_entries: int = 1000         # per package fetch cap
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

    # Worker (centralised ingestion)
    worker_concurrency: int = 10

    model_config = {"env_prefix": ""}


settings = Settings()

"""Central, validated settings. Every tunable in the system comes from here."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IMPACT_", env_file=".env", extra="forbid")

    # Ingestion limits
    max_repo_size_mb: int = 500
    max_file_count: int = 20_000
    clone_timeout_seconds: int = 120
    supported_languages: tuple[str, ...] = ("python", "go")

    # Ignore patterns applied on top of any repo-level ignore file
    default_ignore_globs: tuple[str, ...] = (
        "**/node_modules/**",
        "**/vendor/**",
        "**/.git/**",
        "**/dist/**",
        "**/build/**",
        "**/*.min.js",
        "**/venv/**",
        "**/.venv/**",
        "**/__pycache__/**",
    )

    # Graph traversal safety caps (used in later phases, declared now so nothing
    # downstream can silently omit them)
    max_traversal_depth: int = 4
    max_traversal_nodes: int = 500


settings = Settings()

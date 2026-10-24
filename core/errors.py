"""Typed error hierarchy. No bare exceptions are raised anywhere in this codebase;
everything that can fail raises from one of these so callers can handle failure
modes explicitly instead of catching Exception."""


class ImpactEngineError(Exception):
    """Base class for all domain errors in this system."""


class RepoTooLargeError(ImpactEngineError):
    """Raised when a repo exceeds configured size/file-count limits, checked
    before a clone is allowed to complete."""

    def __init__(self, reason: str, limit: int, actual: int):
        self.reason = reason
        self.limit = limit
        self.actual = actual
        super().__init__(f"Repo rejected: {reason} (limit={limit}, actual={actual})")


class CloneTimeoutError(ImpactEngineError):
    """Raised when a git clone/fetch exceeds the configured timeout."""


class PathEscapeError(ImpactEngineError):
    """Raised when a resolved file path falls outside the repo root — covers
    both '../' traversal and symlink escape attempts. Treated as a security
    event, not a normal parse failure."""

    def __init__(self, attempted_path: str, repo_root: str):
        self.attempted_path = attempted_path
        self.repo_root = repo_root
        super().__init__(f"Path escapes repo root: {attempted_path!r} not under {repo_root!r}")


class UnsupportedLanguageError(ImpactEngineError):
    """Raised internally when a parser is requested for a language with no
    registered grammar. Callers should catch this and record the file as
    unparsed rather than letting it propagate."""


class GraphQueryError(ImpactEngineError):
    """Raised by GraphStore implementations on query failure."""


class TraversalLimitError(ImpactEngineError):
    """Raised internally when a traversal would exceed configured caps and
    the caller has requested strict mode instead of truncation."""


class VectorStoreError(ImpactEngineError):
    """Raised by VectorStore implementations on failure."""

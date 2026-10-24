"""Interfaces for every I/O boundary. Core and services depend only on these,
never on a concrete adapter, so storage/parsing engines are swappable and
testable with fakes instead of mocking third-party internals."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.models import (
    FileParseResult,
    GraphEdge,
    GraphNode,
    Language,
    TraversalResult,
    UnparsedFile,
)


class RepoSource(ABC):
    """Fetches repository content onto local disk. Must enforce size/time
    limits itself — callers assume a returned path is safe to walk."""

    @abstractmethod
    def fetch(self, repo_url: str, ref: str, dest: Path) -> str:
        """Clone/checkout `ref` of `repo_url` into `dest`. Returns the resolved
        commit SHA. Raises RepoTooLargeError or CloneTimeoutError on limit breach."""

    @abstractmethod
    def diff_paths(self, repo_path: Path, base_sha: str, head_sha: str) -> list[str]:
        """Return file paths changed between two commits, relative to repo root."""


class Parser(ABC):
    """Parses a single file's source into structured definitions. Must never
    execute or import the source it is given — parsing only."""

    @abstractmethod
    def supports(self, language: Language) -> bool: ...

    @abstractmethod
    def parse_file(self, path: str, source: bytes, language: Language) -> FileParseResult:
        """Raises UnsupportedLanguageError or wraps syntax errors — callers
        catch and convert to UnparsedFile, this method itself should not
        swallow the error silently."""


class RepoWalker(ABC):
    """Enumerates parseable files in a repo, applying ignore rules and safety
    checks (traversal/symlink escape) before any file is opened."""

    @abstractmethod
    def walk(self, repo_root: Path) -> list[Path]:
        """Returns absolute paths of files to parse. Every returned path is
        guaranteed to resolve inside repo_root."""


class UnparsedFileSink(ABC):
    """Not an adapter with I/O of its own — kept as a port so the ingestion
    service can be tested without depending on ParseResult construction
    directly. Collects unparsed files during a run."""

    @abstractmethod
    def record(self, unparsed: UnparsedFile) -> None: ...

    @abstractmethod
    def all(self) -> list[UnparsedFile]: ...


class GraphStore(ABC):
    """Stores and queries the dependency graph. Every method that returns
    nodes/edges must filter by repo_id at the query level — never fetch
    broadly and filter in application code, since that pattern is exactly
    how cross-tenant leaks happen. Every traversal method must enforce a
    depth and node-count cap and report truncation rather than running
    unbounded."""

    @abstractmethod
    def upsert_nodes(self, repo_id: str, nodes: list[GraphNode]) -> None: ...

    @abstractmethod
    def upsert_edges(self, repo_id: str, edges: list[GraphEdge]) -> None: ...

    @abstractmethod
    def replace_file_subgraph(
        self, repo_id: str, file_path: str, nodes: list[GraphNode], edges: list[GraphEdge]
    ) -> None:
        """Atomically replace all nodes/edges that were DEFINED_IN this file
        with the new set. Used for incremental reindexing so changed files
        never leave orphaned nodes from a previous version behind."""

    @abstractmethod
    def get_node(self, repo_id: str, node_id: str) -> GraphNode | None: ...

    @abstractmethod
    def direct_callers(self, repo_id: str, node_id: str) -> list[GraphNode]:
        """Nodes with a CALLS edge pointing at node_id."""

    @abstractmethod
    def direct_callees(self, repo_id: str, node_id: str) -> list[GraphNode]:
        """Nodes that node_id has a CALLS edge pointing to."""

    @abstractmethod
    def transitive_impact(
        self, repo_id: str, node_id: str, max_depth: int, max_nodes: int
    ) -> TraversalResult:
        """Bounded transitive closure of callers (who is affected if
        node_id changes). Must return truncated=True rather than raising
        or silently capping when the true result exceeds the caps."""

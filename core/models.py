"""Domain models. Pure data — no I/O, no dependency on any adapter."""
from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, ConfigDict


class Language(str, Enum):
    PYTHON = "python"
    GO = "go"


class UnparsedReason(str, Enum):
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    SYNTAX_ERROR = "syntax_error"
    FILE_TOO_LARGE = "file_too_large"
    READ_ERROR = "read_error"
    PATH_REJECTED = "path_rejected"


class UnparsedFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    reason: UnparsedReason
    detail: str = ""


class FunctionDef(BaseModel):
    """A single function/method extracted from source. qualified_name is the
    stable identity used everywhere downstream (graph nodes, embeddings)."""

    model_config = ConfigDict(frozen=True)

    qualified_name: str  # e.g. "pkg.module.ClassName.method_name"
    name: str
    file_path: str
    language: Language
    start_line: int
    end_line: int
    body_hash: str  # sha256 of normalized body — used for change/cache detection
    docstring: str | None = None
    calls: tuple[str, ...] = ()  # unresolved callee names as written in source

    @staticmethod
    def hash_body(body: str) -> str:
        normalized = "\n".join(line.rstrip() for line in body.strip().splitlines())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ClassDef(BaseModel):
    model_config = ConfigDict(frozen=True)
    qualified_name: str
    name: str
    file_path: str
    language: Language
    start_line: int
    end_line: int
    bases: tuple[str, ...] = ()


class ImportDef(BaseModel):
    model_config = ConfigDict(frozen=True)
    file_path: str
    imported: str  # module or package name as written
    line: int


class FileParseResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    language: Language
    functions: tuple[FunctionDef, ...] = ()
    classes: tuple[ClassDef, ...] = ()
    imports: tuple[ImportDef, ...] = ()


class ParseResult(BaseModel):
    """Result of parsing a repository (or a changeset within it). Carries
    unparsed files explicitly — this must never be dropped or hidden from
    the caller, per project rules."""

    model_config = ConfigDict(frozen=True)

    repo_id: str
    commit_sha: str
    files: tuple[FileParseResult, ...] = ()
    unparsed_files: tuple[UnparsedFile, ...] = ()

    @property
    def is_complete(self) -> bool:
        return len(self.unparsed_files) == 0

    @property
    def total_functions(self) -> int:
        return sum(len(f.functions) for f in self.files)


# ---- Graph layer models (Phase 2) ----


class NodeKind(str, Enum):
    FILE = "file"
    FUNCTION = "function"
    CLASS = "class"
    UNRESOLVED_SYMBOL = "unresolved_symbol"


class EdgeKind(str, Enum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    DEFINED_IN = "defined_in"


class GraphNode(BaseModel):
    """A node in the dependency graph. `node_id` is globally unique and is
    always namespaced by repo_id so cross-repo collisions are structurally
    impossible, not just avoided by convention."""

    model_config = ConfigDict(frozen=True)

    node_id: str  # f"{repo_id}:{kind}:{qualified_name_or_path}"
    repo_id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    @staticmethod
    def make_id(repo_id: str, kind: NodeKind, identity: str) -> str:
        return f"{repo_id}:{kind.value}:{identity}"


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo_id: str
    source_id: str
    target_id: str
    kind: EdgeKind


class TraversalResult(BaseModel):
    """Result of a bounded graph traversal. `truncated` must be checked by
    every caller — a truncated result is not a complete answer and must
    never be presented as one."""

    model_config = ConfigDict(frozen=True)

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    truncated: bool = False

"""Builds graph nodes and edges from a ParseResult and writes them into a
GraphStore. This is where call resolution happens: Phase 1 only recorded
call sites by bare name (e.g. "compute"); here we resolve those names to
actual qualified functions within the same repo where possible, and fall
back to an UnresolvedSymbol node otherwise — never silently dropped."""
from __future__ import annotations

from core.models import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    ParseResult,
)
from core.ports import GraphStore


def _file_node(repo_id: str, path: str) -> GraphNode:
    node_id = GraphNode.make_id(repo_id, NodeKind.FILE, path)
    return GraphNode(node_id=node_id, repo_id=repo_id, kind=NodeKind.FILE, name=path,
                      qualified_name=path, file_path=path)


def build_graph_for_repo(repo_id: str, parse_result: ParseResult, store: GraphStore) -> None:
    """Full (non-incremental) build: writes every file's subgraph. Uses
    replace_file_subgraph per file so this is also safe to call again on
    the same repo_id for a subsequent commit."""
    # First pass: build a lookup of qualified_name -> node_id for every
    # function in the repo, so call resolution can happen in the second pass
    # even when the caller and callee are defined in different files.
    name_index: dict[str, str] = {}
    simple_name_index: dict[str, list[str]] = {}

    for file_result in parse_result.files:
        for fn in file_result.functions:
            node_id = GraphNode.make_id(repo_id, NodeKind.FUNCTION, fn.qualified_name)
            name_index[fn.qualified_name] = node_id
            simple_name_index.setdefault(fn.name, []).append(node_id)

    for file_result in parse_result.files:
        nodes: list[GraphNode] = [_file_node(repo_id, file_result.path)]
        edges: list[GraphEdge] = []
        file_node_id = nodes[0].node_id

        for cls in file_result.classes:
            class_node_id = GraphNode.make_id(repo_id, NodeKind.CLASS, cls.qualified_name)
            nodes.append(
                GraphNode(
                    node_id=class_node_id, repo_id=repo_id, kind=NodeKind.CLASS,
                    name=cls.name, qualified_name=cls.qualified_name,
                    file_path=cls.file_path, start_line=cls.start_line, end_line=cls.end_line,
                )
            )
            edges.append(
                GraphEdge(repo_id=repo_id, source_id=class_node_id,
                          target_id=file_node_id, kind=EdgeKind.DEFINED_IN)
            )
            for base in cls.bases:
                base_id = name_index.get(base) or _unresolved_id(repo_id, base)
                if base_id not in {n.node_id for n in nodes}:
                    nodes.append(_maybe_unresolved_node(repo_id, base, name_index))
                edges.append(
                    GraphEdge(repo_id=repo_id, source_id=class_node_id,
                              target_id=base_id, kind=EdgeKind.INHERITS)
                )

        for fn in file_result.functions:
            fn_node_id = GraphNode.make_id(repo_id, NodeKind.FUNCTION, fn.qualified_name)
            nodes.append(
                GraphNode(
                    node_id=fn_node_id, repo_id=repo_id, kind=NodeKind.FUNCTION,
                    name=fn.name, qualified_name=fn.qualified_name,
                    file_path=fn.file_path, start_line=fn.start_line, end_line=fn.end_line,
                )
            )
            edges.append(
                GraphEdge(repo_id=repo_id, source_id=fn_node_id,
                          target_id=file_node_id, kind=EdgeKind.DEFINED_IN)
            )

            for called_name in fn.calls:
                target_id = _resolve_call(called_name, name_index, simple_name_index)
                if target_id is None:
                    target_id = _unresolved_id(repo_id, called_name)
                    unresolved_node = GraphNode(
                        node_id=target_id, repo_id=repo_id, kind=NodeKind.UNRESOLVED_SYMBOL,
                        name=called_name, qualified_name=called_name,
                    )
                    if unresolved_node.node_id not in {n.node_id for n in nodes}:
                        nodes.append(unresolved_node)
                edges.append(
                    GraphEdge(repo_id=repo_id, source_id=fn_node_id,
                              target_id=target_id, kind=EdgeKind.CALLS)
                )

        for imp in file_result.imports:
            import_id = GraphNode.make_id(repo_id, NodeKind.UNRESOLVED_SYMBOL, imp.imported)
            if import_id not in {n.node_id for n in nodes}:
                nodes.append(
                    GraphNode(node_id=import_id, repo_id=repo_id,
                              kind=NodeKind.UNRESOLVED_SYMBOL, name=imp.imported,
                              qualified_name=imp.imported)
                )
            edges.append(
                GraphEdge(repo_id=repo_id, source_id=file_node_id,
                          target_id=import_id, kind=EdgeKind.IMPORTS)
            )

        store.replace_file_subgraph(repo_id, file_result.path, nodes, edges)


def _unresolved_id(repo_id: str, name: str) -> str:
    return GraphNode.make_id(repo_id, NodeKind.UNRESOLVED_SYMBOL, name)


def _maybe_unresolved_node(repo_id: str, name: str, name_index: dict[str, str]) -> GraphNode:
    return GraphNode(
        node_id=_unresolved_id(repo_id, name), repo_id=repo_id,
        kind=NodeKind.UNRESOLVED_SYMBOL, name=name, qualified_name=name,
    )


def _resolve_call(
    called_name: str, name_index: dict[str, str], simple_name_index: dict[str, list[str]]
) -> str | None:
    """Best-effort resolution: exact qualified-name match first, then a
    unique simple-name match. An ambiguous simple name (multiple functions
    share it, e.g. two classes both define `.format`) is deliberately left
    unresolved rather than guessing wrong — a wrong edge is worse than a
    missing one, since it would corrupt every impact-analysis answer built
    on top of it."""
    if called_name in name_index:
        return name_index[called_name]
    candidates = simple_name_index.get(called_name)
    if candidates and len(candidates) == 1:
        return candidates[0]
    return None

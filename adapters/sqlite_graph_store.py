"""SQLite-backed GraphStore. Stands in for Neo4j in this phase — same port
interface, same guarantees (repo_id isolation, bounded traversal, no string-
built queries), so swapping to a real graph database later is an adapter
swap, not a rewrite of anything that depends on GraphStore.

Every query in this file is parameterized. None of them string-interpolate
a value into SQL. This is the property the project's security rules require
and it holds here exactly as it must hold for a Cypher-backed store.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.errors import GraphQueryError
from core.models import EdgeKind, GraphEdge, GraphNode, NodeKind, TraversalResult
from core.ports import GraphStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT,
    start_line INTEGER,
    end_line INTEGER,
    PRIMARY KEY (repo_id, node_id)
);

CREATE TABLE IF NOT EXISTS edges (
    repo_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (repo_id, source_id, target_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(repo_id, file_path);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(repo_id, source_id, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(repo_id, target_id, kind);
"""


class SqliteGraphStore(GraphStore):
    def __init__(self, db_path: str | Path = ":memory:"):
        # check_same_thread=False is safe here because callers are expected
        # to own one store instance per worker/process, not to share a
        # connection across threads concurrently.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_nodes(self, repo_id: str, nodes: list[GraphNode]) -> None:
        try:
            self._conn.executemany(
                """
                INSERT INTO nodes (node_id, repo_id, kind, name, qualified_name,
                                    file_path, start_line, end_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, node_id) DO UPDATE SET
                    kind=excluded.kind, name=excluded.name,
                    qualified_name=excluded.qualified_name, file_path=excluded.file_path,
                    start_line=excluded.start_line, end_line=excluded.end_line
                """,
                [
                    (
                        n.node_id, repo_id, n.kind.value, n.name, n.qualified_name,
                        n.file_path, n.start_line, n.end_line,
                    )
                    for n in nodes
                ],
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise GraphQueryError(f"upsert_nodes failed: {exc}") from exc

    def upsert_edges(self, repo_id: str, edges: list[GraphEdge]) -> None:
        try:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO edges (repo_id, source_id, target_id, kind)
                VALUES (?, ?, ?, ?)
                """,
                [(repo_id, e.source_id, e.target_id, e.kind.value) for e in edges],
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise GraphQueryError(f"upsert_edges failed: {exc}") from exc

    def replace_file_subgraph(
        self, repo_id: str, file_path: str, nodes: list[GraphNode], edges: list[GraphEdge]
    ) -> None:
        try:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            # Delete this file's nodes and any edges touching them, then
            # insert the fresh set — atomic so a partial reindex never
            # leaves stale nodes from a previous version of the file behind.
            old_node_ids = [
                row[0]
                for row in cur.execute(
                    "SELECT node_id FROM nodes WHERE repo_id = ? AND file_path = ?",
                    (repo_id, file_path),
                )
            ]
            if old_node_ids:
                placeholders = ",".join("?" for _ in old_node_ids)
                cur.execute(
                    f"DELETE FROM edges WHERE repo_id = ? AND "
                    f"(source_id IN ({placeholders}) OR target_id IN ({placeholders}))",
                    [repo_id, *old_node_ids, *old_node_ids],
                )
                cur.execute(
                    "DELETE FROM nodes WHERE repo_id = ? AND file_path = ?",
                    (repo_id, file_path),
                )
            cur.executemany(
                """
                INSERT INTO nodes (node_id, repo_id, kind, name, qualified_name,
                                    file_path, start_line, end_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        n.node_id, repo_id, n.kind.value, n.name, n.qualified_name,
                        n.file_path, n.start_line, n.end_line,
                    )
                    for n in nodes
                ],
            )
            cur.executemany(
                """
                INSERT OR IGNORE INTO edges (repo_id, source_id, target_id, kind)
                VALUES (?, ?, ?, ?)
                """,
                [(repo_id, e.source_id, e.target_id, e.kind.value) for e in edges],
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise GraphQueryError(f"replace_file_subgraph failed: {exc}") from exc

    def get_node(self, repo_id: str, node_id: str) -> GraphNode | None:
        row = self._conn.execute(
            "SELECT node_id, kind, name, qualified_name, file_path, start_line, end_line "
            "FROM nodes WHERE repo_id = ? AND node_id = ?",
            (repo_id, node_id),
        ).fetchone()
        return self._row_to_node(row) if row else None

    def direct_callers(self, repo_id: str, node_id: str) -> list[GraphNode]:
        rows = self._conn.execute(
            """
            SELECT n.node_id, n.kind, n.name, n.qualified_name, n.file_path,
                   n.start_line, n.end_line
            FROM edges e JOIN nodes n ON n.repo_id = e.repo_id AND n.node_id = e.source_id
            WHERE e.repo_id = ? AND e.target_id = ? AND e.kind = ?
            """,
            (repo_id, node_id, EdgeKind.CALLS.value),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def direct_callees(self, repo_id: str, node_id: str) -> list[GraphNode]:
        rows = self._conn.execute(
            """
            SELECT n.node_id, n.kind, n.name, n.qualified_name, n.file_path,
                   n.start_line, n.end_line
            FROM edges e JOIN nodes n ON n.repo_id = e.repo_id AND n.node_id = e.target_id
            WHERE e.repo_id = ? AND e.source_id = ? AND e.kind = ?
            """,
            (repo_id, node_id, EdgeKind.CALLS.value),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def transitive_impact(
        self, repo_id: str, node_id: str, max_depth: int, max_nodes: int
    ) -> TraversalResult:
        """BFS over callers, bounded by depth and total node count. Every
        query inside the loop is still parameterized and still filters on
        repo_id — bounding the traversal does not relax that."""
        visited_nodes: dict[str, GraphNode] = {}
        visited_edges: list[GraphEdge] = []
        frontier = [node_id]
        truncated = False

        for _depth in range(max_depth):
            if not frontier:
                break
            next_frontier: list[str] = []
            for current in frontier:
                callers = self.direct_callers(repo_id, current)
                for caller in callers:
                    edge = GraphEdge(
                        repo_id=repo_id, source_id=caller.node_id,
                        target_id=current, kind=EdgeKind.CALLS,
                    )
                    if caller.node_id not in visited_nodes:
                        if len(visited_nodes) >= max_nodes:
                            truncated = True
                            continue
                        visited_nodes[caller.node_id] = caller
                        next_frontier.append(caller.node_id)
                    visited_edges.append(edge)
            frontier = next_frontier
            if len(visited_nodes) >= max_nodes:
                truncated = truncated or bool(frontier)
                break
        else:
            # loop completed all max_depth iterations — if there's still a
            # frontier left to explore, we stopped due to depth, not because
            # the graph was exhausted, so report truncation honestly.
            if frontier:
                truncated = True

        return TraversalResult(
            nodes=tuple(visited_nodes.values()), edges=tuple(visited_edges), truncated=truncated
        )

    @staticmethod
    def _row_to_node(row: tuple) -> GraphNode:
        node_id, kind, name, qualified_name, file_path, start_line, end_line = row
        return GraphNode(
            node_id=node_id,
            repo_id=node_id.split(":", 1)[0],
            kind=NodeKind(kind),
            name=name,
            qualified_name=qualified_name,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
        )

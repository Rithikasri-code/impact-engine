from adapters.sqlite_graph_store import SqliteGraphStore
from core.models import EdgeKind, GraphEdge, GraphNode, NodeKind


def make_fn_node(repo_id: str, name: str) -> GraphNode:
    return GraphNode(
        node_id=GraphNode.make_id(repo_id, NodeKind.FUNCTION, name),
        repo_id=repo_id, kind=NodeKind.FUNCTION, name=name, qualified_name=name,
    )


def test_upsert_and_get_node():
    store = SqliteGraphStore()
    node = make_fn_node("repo1", "foo")
    store.upsert_nodes("repo1", [node])
    fetched = store.get_node("repo1", node.node_id)
    assert fetched is not None
    assert fetched.name == "foo"


def test_direct_callers_and_callees():
    store = SqliteGraphStore()
    a = make_fn_node("repo1", "a")
    b = make_fn_node("repo1", "b")
    store.upsert_nodes("repo1", [a, b])
    store.upsert_edges("repo1", [GraphEdge(repo_id="repo1", source_id=a.node_id,
                                            target_id=b.node_id, kind=EdgeKind.CALLS)])

    callers_of_b = store.direct_callers("repo1", b.node_id)
    assert {n.node_id for n in callers_of_b} == {a.node_id}

    callees_of_a = store.direct_callees("repo1", a.node_id)
    assert {n.node_id for n in callees_of_a} == {b.node_id}


def test_cross_tenant_isolation():
    """A node/edge in repo1 must never be visible when querying repo2, even
    if an attacker crafts a node_id that happens to collide. This is the
    single most important property of this store."""
    store = SqliteGraphStore()
    a1 = make_fn_node("repo1", "shared_name")
    a2 = make_fn_node("repo2", "shared_name")
    store.upsert_nodes("repo1", [a1])
    store.upsert_nodes("repo2", [a2])

    # Same qualified name, different repo -> different node_id (repo-prefixed)
    assert a1.node_id != a2.node_id

    # Querying repo2 for repo1's node_id must return nothing.
    assert store.get_node("repo2", a1.node_id) is None
    assert store.get_node("repo1", a1.node_id) is not None


def test_cross_tenant_isolation_in_callers():
    store = SqliteGraphStore()
    a = make_fn_node("repo1", "a")
    b = make_fn_node("repo1", "b")
    store.upsert_nodes("repo1", [a, b])
    store.upsert_edges("repo1", [GraphEdge(repo_id="repo1", source_id=a.node_id,
                                            target_id=b.node_id, kind=EdgeKind.CALLS)])

    # Querying with a different repo_id for the same target node_id string
    # must return no callers — proves the query filters by repo_id and
    # doesn't just trust the node_id's embedded prefix.
    assert store.direct_callers("repo2", b.node_id) == []


def test_replace_file_subgraph_removes_stale_nodes():
    store = SqliteGraphStore()
    old_node = GraphNode(
        node_id="repo1:function:old_fn", repo_id="repo1", kind=NodeKind.FUNCTION,
        name="old_fn", qualified_name="old_fn", file_path="a.py",
    )
    store.replace_file_subgraph("repo1", "a.py", [old_node], [])
    assert store.get_node("repo1", old_node.node_id) is not None

    new_node = GraphNode(
        node_id="repo1:function:new_fn", repo_id="repo1", kind=NodeKind.FUNCTION,
        name="new_fn", qualified_name="new_fn", file_path="a.py",
    )
    store.replace_file_subgraph("repo1", "a.py", [new_node], [])

    # old_fn was removed when a.py was replaced, not left as an orphan
    assert store.get_node("repo1", old_node.node_id) is None
    assert store.get_node("repo1", new_node.node_id) is not None


def test_transitive_impact_respects_depth_cap():
    """Build a chain a -> b -> c -> d -> e (each calls the next) and confirm
    that a shallow depth cap truncates rather than walking the whole chain."""
    store = SqliteGraphStore()
    names = ["a", "b", "c", "d", "e"]
    nodes = [make_fn_node("repo1", n) for n in names]
    store.upsert_nodes("repo1", nodes)
    edges = [
        GraphEdge(repo_id="repo1", source_id=nodes[i].node_id,
                  target_id=nodes[i + 1].node_id, kind=EdgeKind.CALLS)
        for i in range(len(nodes) - 1)
    ]
    store.upsert_edges("repo1", edges)

    # Impact of changing "e": who transitively calls it? Chain is a->b->c->d->e,
    # so callers-of-e at depth 1 = {d}, depth 2 = {c}, etc.
    result = store.transitive_impact("repo1", nodes[-1].node_id, max_depth=2, max_nodes=100)
    result_names = {n.name for n in result.nodes}
    assert result_names == {"c", "d"}
    assert result.truncated is True


def test_transitive_impact_not_truncated_when_full_graph_fits():
    store = SqliteGraphStore()
    a = make_fn_node("repo1", "a")
    b = make_fn_node("repo1", "b")
    store.upsert_nodes("repo1", [a, b])
    store.upsert_edges("repo1", [GraphEdge(repo_id="repo1", source_id=a.node_id,
                                            target_id=b.node_id, kind=EdgeKind.CALLS)])

    result = store.transitive_impact("repo1", b.node_id, max_depth=5, max_nodes=100)
    assert {n.name for n in result.nodes} == {"a"}
    assert result.truncated is False


def test_transitive_impact_respects_node_count_cap():
    store = SqliteGraphStore()
    # Fan-in: many callers of one target
    target = make_fn_node("repo1", "target")
    callers = [make_fn_node("repo1", f"caller_{i}") for i in range(10)]
    store.upsert_nodes("repo1", [target, *callers])
    store.upsert_edges(
        "repo1",
        [GraphEdge(repo_id="repo1", source_id=c.node_id, target_id=target.node_id,
                   kind=EdgeKind.CALLS) for c in callers],
    )

    result = store.transitive_impact("repo1", target.node_id, max_depth=5, max_nodes=3)
    assert len(result.nodes) <= 3
    assert result.truncated is True

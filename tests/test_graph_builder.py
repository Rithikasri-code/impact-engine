from adapters.sqlite_graph_store import SqliteGraphStore
from adapters.treesitter_parser import TreeSitterParser
from core.models import GraphNode, Language, NodeKind, ParseResult
from services.graph_builder import build_graph_for_repo


def _parse_result_from_source(repo_id: str, files: dict[str, bytes]) -> ParseResult:
    parser = TreeSitterParser()
    parsed = [
        parser.parse_file(path, source, Language.PYTHON) for path, source in files.items()
    ]
    return ParseResult(repo_id=repo_id, commit_sha="test", files=tuple(parsed))


def test_build_graph_resolves_call_within_same_file():
    source = b'''
def compute(name):
    return "hello " + name

def main():
    return compute("world")
'''
    parse_result = _parse_result_from_source("repo1", {"m.py": source})
    store = SqliteGraphStore()
    build_graph_for_repo("repo1", parse_result, store)

    main_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "main")
    compute_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "compute")

    callees = store.direct_callees("repo1", main_id)
    assert compute_id in {n.node_id for n in callees}
    # The resolved callee should be a real FUNCTION node, not unresolved.
    resolved = store.get_node("repo1", compute_id)
    assert resolved is not None
    assert resolved.kind == NodeKind.FUNCTION


def test_build_graph_resolves_call_across_files():
    main_src = b'''
from pkg.helper import compute

def main():
    return compute("world")
'''
    helper_src = b'''
def compute(name):
    return "hello " + name
'''
    parse_result = _parse_result_from_source(
        "repo1", {"main.py": main_src, "pkg/helper.py": helper_src}
    )
    store = SqliteGraphStore()
    build_graph_for_repo("repo1", parse_result, store)

    main_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "main")
    compute_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "compute")

    callees = store.direct_callees("repo1", main_id)
    assert compute_id in {n.node_id for n in callees}


def test_build_graph_leaves_ambiguous_calls_unresolved_rather_than_guessing():
    """Two different classes both define a `.run` method. A call to `.run`
    is genuinely ambiguous from name alone — the builder must not silently
    pick one, since a wrong edge corrupts every impact answer downstream."""
    source = b'''
class A:
    def run(self):
        return 1

class B:
    def run(self):
        return 2

def caller(x):
    return x.run()
'''
    parse_result = _parse_result_from_source("repo1", {"m.py": source})
    store = SqliteGraphStore()
    build_graph_for_repo("repo1", parse_result, store)

    caller_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "caller")
    callees = store.direct_callees("repo1", caller_id)
    kinds = {n.kind for n in callees}
    assert NodeKind.UNRESOLVED_SYMBOL in kinds
    # Neither A.run nor B.run should have been guessed as the target.
    resolved_function_names = {n.qualified_name for n in callees if n.kind == NodeKind.FUNCTION}
    assert "A.run" not in resolved_function_names
    assert "B.run" not in resolved_function_names


def test_build_graph_records_unknown_external_call_as_unresolved():
    source = b'''
def main():
    return some_external_library_function()
'''
    parse_result = _parse_result_from_source("repo1", {"m.py": source})
    store = SqliteGraphStore()
    build_graph_for_repo("repo1", parse_result, store)

    main_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "main")
    callees = store.direct_callees("repo1", main_id)
    assert len(callees) == 1
    assert callees[0].kind == NodeKind.UNRESOLVED_SYMBOL
    assert callees[0].name == "some_external_library_function"


def test_rebuilding_repo_replaces_rather_than_duplicates():
    """Calling build_graph_for_repo twice for the same repo/file must not
    leave two copies of the same function node behind (simulates reindexing
    after a new commit with no changes)."""
    source = b"def f():\n    return 1\n"
    parse_result = _parse_result_from_source("repo1", {"m.py": source})
    store = SqliteGraphStore()

    build_graph_for_repo("repo1", parse_result, store)
    build_graph_for_repo("repo1", parse_result, store)

    f_id = GraphNode.make_id("repo1", NodeKind.FUNCTION, "f")
    node = store.get_node("repo1", f_id)
    assert node is not None  # still resolvable, not duplicated into ambiguity

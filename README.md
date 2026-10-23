# Impact Engine — Phase 1 & 2: Ingestion, Parsing, and the Dependency Graph

Given a codebase, extract every function, class, and import into a structured,
queryable form — the foundation for the dependency graph and semantic search
layers built in later phases.

## What's implemented in this phase

- **Safe repo walking** (`adapters/fs_walker.py`): discovers `.py`/`.go` files
  in a directory tree while defending against path traversal and symlink
  escape attacks. Every repo we index is treated as untrusted input.
- **Tree-sitter parsing** (`adapters/treesitter_parser.py`): extracts
  functions (with qualified names, call sites, docstrings, and a content
  hash for change detection), classes, and imports for Python and Go.
  Parsing only — never executes or imports the code it reads.
- **Git-backed fetching** (`adapters/git_source.py`): clones a repo with a
  timeout and rejects it if it exceeds a configured size after cloning.
- **Ingestion orchestration** (`services/ingestion.py`): ties the above
  together into one `ParseResult` that is honest about what it could and
  couldn't parse — unparsed files are never silently dropped.

## Why it's structured this way

Everything that talks to the outside world (filesystem, git, parser
libraries) sits behind an interface in `core/ports.py`. The services and
core logic never import a concrete adapter directly. This is what lets
Phase 2 swap in Neo4j and Phase 3 swap in pgvector without touching this
code, and it's what lets every module here be tested with fakes instead of
mocking library internals.

## Running it

```bash
pip install -e ".[dev]"
pytest -v
ruff check core/ adapters/ services/ api/
```

## Phase 2: the dependency graph

- **`core/ports.py` — `GraphStore`**: the interface every graph backend must
  implement. Every method that returns nodes/edges is required to filter by
  `repo_id` at the query level, and traversal methods must enforce a depth
  and node-count cap and report truncation honestly instead of running
  unbounded.
- **`adapters/sqlite_graph_store.py`**: a real, working implementation of
  that interface. Every query is parameterized — no string-built SQL
  anywhere. `replace_file_subgraph` is atomic, so reindexing a changed file
  never leaves orphaned nodes from the old version behind. `transitive_impact`
  does a bounded BFS over callers and sets `truncated=True` honestly when
  the cap is hit.
- **`services/graph_builder.py`**: turns a `ParseResult` into graph nodes and
  edges, and does call resolution — matching a bare call name (`compute()`)
  to the actual function it refers to, first by exact qualified name, then
  by a *unique* simple-name match. An ambiguous call is left as an
  `UNRESOLVED_SYMBOL` node rather than guessed, since a wrong edge would
  silently corrupt every impact-analysis answer built on top of it later.

### Try it

```python
from adapters.sqlite_graph_store import SqliteGraphStore
from adapters.fs_walker import SafeRepoWalker
from adapters.treesitter_parser import TreeSitterParser
from core.config import Settings
from core.models import GraphNode, NodeKind
from services.ingestion import ingest_repo
from services.graph_builder import build_graph_for_repo
from pathlib import Path

parse_result = ingest_repo(
    Path("tests/fixtures/sample_repo"), "demo", "local",
    SafeRepoWalker(Settings()), TreeSitterParser(),
)

store = SqliteGraphStore()
build_graph_for_repo("demo", parse_result, store)

main_id = GraphNode.make_id("demo", NodeKind.FUNCTION, "main")
print(store.direct_callees("demo", main_id))
```


## Try it on the fixture repo

```python
from pathlib import Path
from adapters.fs_walker import SafeRepoWalker
from adapters.treesitter_parser import TreeSitterParser
from core.config import Settings
from services.ingestion import ingest_repo

result = ingest_repo(
    repo_root=Path("tests/fixtures/sample_repo"),
    repo_id="demo",
    commit_sha="local",
    walker=SafeRepoWalker(Settings()),
    parser=TreeSitterParser(),
)

print(f"Parsed {len(result.files)} files, {result.total_functions} functions")
print(f"Unparsed: {result.unparsed_files}")
```

## What's NOT built yet (by design — later phases)

- Semantic embeddings + vector search (Phase 3)
- Hybrid ranking / impact analysis combining graph + semantic (Phase 4)
- Diff-aware queries, test-coverage mapping (Phase 5)
- Historical-commit evaluation harness (Phase 6)
- API, CLI, PR bot (Phase 7)

## Known limitations in this phase

- Call resolution is name-based: exact qualified-name match, then a unique
  simple-name match. An ambiguous call (two classes both define `.run`, and
  the caller invokes `.run` on a variable) is deliberately left as an
  `UNRESOLVED_SYMBOL` node rather than guessed — a wrong edge would corrupt
  every impact answer built on top of it, so "don't know" beats "guess wrong."
  True resolution (type inference to know *which* `.run`) is a real
  limitation, not a bug, and worth naming explicitly in a portfolio writeup.
- Only Python and Go are supported, by design, to keep scope defensible for
  a first pass — more tree-sitter grammars are a config change away.
- The `GraphStore` in this phase is SQLite-backed, not Neo4j. It implements
  the identical port interface (`core/ports.py`) with the identical
  guarantees (repo_id isolation on every query, bounded/truncating
  traversal, no string-built queries) — swapping in a real Neo4j adapter
  later means writing one new file, not touching anything that depends on
  `GraphStore`. This was a deliberate substitution for this environment
  (no way to run a live Neo4j server here), not a shortcut on the actual
  guarantees the port requires.

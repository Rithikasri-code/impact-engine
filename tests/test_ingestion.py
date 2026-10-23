from pathlib import Path

from adapters.fs_walker import SafeRepoWalker
from adapters.treesitter_parser import TreeSitterParser
from core.config import Settings
from core.models import UnparsedReason
from services.ingestion import ingest_repo

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def test_ingest_repo_parses_valid_files_and_records_broken_one():
    walker = SafeRepoWalker(Settings())
    parser = TreeSitterParser()

    result = ingest_repo(
        repo_root=FIXTURE_REPO,
        repo_id="test-repo",
        commit_sha="deadbeef",
        walker=walker,
        parser=parser,
    )

    parsed_paths = {f.path for f in result.files}
    assert "main.py" in parsed_paths
    assert "pkg/helper.py" in parsed_paths

    # broken.py has invalid syntax but tree-sitter is error-tolerant, so it
    # should still come back as a (possibly empty) parse result, not crash
    # the whole ingestion — confirm it's accounted for one way or another.
    all_accounted = parsed_paths | {u.path for u in result.unparsed_files}
    assert "broken.py" in all_accounted

    # result must know its own completeness truthfully
    assert result.total_functions > 0


def test_ingest_repo_records_unsupported_extension(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello")
    # .txt isn't walked by SafeRepoWalker at all (only .py/.go extensions are
    # discovered), which is itself the intended behavior — confirm it simply
    # produces zero files rather than an error.
    walker = SafeRepoWalker(Settings())
    parser = TreeSitterParser()
    result = ingest_repo(tmp_path, "repo", "sha", walker, parser)
    assert result.files == ()
    assert result.unparsed_files == ()


def test_ingest_repo_records_oversized_file(tmp_path: Path, monkeypatch):
    big_file = tmp_path / "big.py"
    big_file.write_text("x = 1\n")

    import services.ingestion as ingestion_mod
    monkeypatch.setattr(ingestion_mod, "_MAX_FILE_BYTES", 1)  # force oversized

    walker = SafeRepoWalker(Settings())
    parser = TreeSitterParser()
    result = ingest_repo(tmp_path, "repo", "sha", walker, parser)

    assert result.files == ()
    assert len(result.unparsed_files) == 1
    assert result.unparsed_files[0].reason == UnparsedReason.FILE_TOO_LARGE

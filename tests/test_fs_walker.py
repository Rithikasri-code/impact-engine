import os
from pathlib import Path

import pytest

from adapters.fs_walker import SafeRepoWalker, resolve_safe
from core.config import Settings
from core.errors import PathEscapeError


@pytest.fixture
def settings():
    return Settings()


def test_walk_finds_python_and_go_files(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f(): pass")
    (tmp_path / "b.go").write_text("package main")
    (tmp_path / "readme.md").write_text("not code")

    walker = SafeRepoWalker(Settings())
    found = walker.walk(tmp_path)
    names = {p.name for p in found}
    assert names == {"a.py", "b.go"}


def test_walk_skips_default_ignored_dirs(tmp_path: Path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "lib.go").write_text("package vendor")
    (tmp_path / "real.py").write_text("def f(): pass")

    walker = SafeRepoWalker(Settings())
    found = walker.walk(tmp_path)
    names = {p.name for p in found}
    assert names == {"real.py"}


def test_resolve_safe_rejects_parent_traversal(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret")

    traversal_path = repo_root / ".." / "outside.py"
    with pytest.raises(PathEscapeError):
        resolve_safe(traversal_path, repo_root)


def test_walk_skips_symlink_escape(tmp_path: Path):
    """A repo containing a symlink pointing outside its own root must not
    have that target read. This is the core security property of the walker."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    secret = tmp_path / "secret.py"
    secret.write_text("SECRET = 'do not read me'")

    link = repo_root / "innocuous.py"
    os.symlink(secret, link)

    walker = SafeRepoWalker(Settings())
    found = walker.walk(repo_root)

    # The symlink must be skipped entirely, not followed.
    assert all(p.resolve() != secret.resolve() for p in found)
    assert len(found) == 0


def test_walk_aborts_over_max_file_count(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("def f(): pass")

    settings = Settings(max_file_count=2)
    walker = SafeRepoWalker(settings)
    with pytest.raises(ValueError, match="max_file_count"):
        walker.walk(tmp_path)

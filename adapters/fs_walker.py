"""Filesystem walker. This is the single place that decides which files get
opened for parsing, and the single place that must defend against a malicious
repo trying to make us read outside its own root."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from core.config import Settings
from core.errors import PathEscapeError
from core.ports import RepoWalker

_LANG_EXTENSIONS = {
    ".py": "python",
    ".go": "go",
}


def resolve_safe(path: Path, repo_root: Path) -> Path:
    """Resolve `path` fully (following symlinks) and assert the result is
    still inside `repo_root`. Raises PathEscapeError otherwise. This is the
    core defense against both '../' traversal and symlink-escape attacks."""
    resolved_root = repo_root.resolve()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PathEscapeError(str(path), str(resolved_root)) from exc

    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PathEscapeError(str(resolved), str(resolved_root)) from exc
    return resolved


class SafeRepoWalker(RepoWalker):
    def __init__(self, settings: Settings, extra_ignore_globs: tuple[str, ...] = ()):
        self._settings = settings
        self._ignore_globs = settings.default_ignore_globs + extra_ignore_globs

    def _is_ignored(self, rel_path: str) -> bool:
        return any(fnmatch.fnmatch(rel_path, pattern) for pattern in self._ignore_globs)

    def _is_dir_ignored(self, rel_dir_path: str) -> bool:
        """Directory pruning needs its own check: a pattern like '**/vendor/**'
        only matches paths *under* vendor/, not the vendor/ path itself, so we
        test against a synthetic child to decide whether to prune the dir."""
        probe = f"{rel_dir_path}/__probe__"
        return self._is_ignored(rel_dir_path) or self._is_ignored(probe)

    def walk(self, repo_root: Path) -> list[Path]:
        repo_root = repo_root.resolve()
        results: list[Path] = []
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(repo_root, followlinks=False):
            # Prune ignored directories before descending, so we never even
            # stat inside e.g. vendor/ or node_modules/.
            rel_dir = os.path.relpath(dirpath, repo_root)
            dirnames[:] = [
                d for d in dirnames
                if not self._is_dir_ignored(os.path.join(rel_dir, d).replace(os.sep, "/"))
            ]

            for fname in filenames:
                ext = os.path.splitext(fname)[1]
                if ext not in _LANG_EXTENSIONS:
                    continue

                rel_path = os.path.join(rel_dir, fname).replace(os.sep, "/")
                if self._is_ignored(rel_path):
                    continue

                candidate = Path(dirpath) / fname

                # Explicitly reject symlinks rather than silently following
                # them — a repo could symlink to /etc/passwd or similar.
                if candidate.is_symlink():
                    continue

                try:
                    safe_path = resolve_safe(candidate, repo_root)
                except PathEscapeError:
                    # A file that escapes the root is a security event, not
                    # a normal skip — re-raise so the caller can log/alert.
                    raise

                file_count += 1
                if file_count > self._settings.max_file_count:
                    raise ValueError(
                        f"Repo exceeds max_file_count={self._settings.max_file_count} "
                        "during walk; aborting rather than partially indexing."
                    )

                results.append(safe_path)

        return results


def language_for_extension(path: Path) -> str | None:
    return _LANG_EXTENSIONS.get(path.suffix)

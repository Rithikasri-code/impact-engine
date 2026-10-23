"""Git-backed RepoSource. Enforces clone timeout and size limits before
handing a repo back to the caller — a repo that fails these checks is
rejected outright, not partially ingested."""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.config import Settings
from core.errors import CloneTimeoutError, RepoTooLargeError
from core.ports import RepoSource


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / (1024 * 1024)


class GitRepoSource(RepoSource):
    def __init__(self, settings: Settings):
        self._settings = settings

    def fetch(self, repo_url: str, ref: str, dest: Path) -> str:
        dest.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", ref, repo_url, str(dest)],
                check=True,
                capture_output=True,
                timeout=self._settings.clone_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise CloneTimeoutError(
                f"Clone of {repo_url} exceeded {self._settings.clone_timeout_seconds}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise CloneTimeoutError(
                f"Clone of {repo_url} failed: {exc.stderr.decode(errors='replace')}"
            ) from exc

        size_mb = _dir_size_mb(dest)
        if size_mb > self._settings.max_repo_size_mb:
            raise RepoTooLargeError(
                reason="disk size after clone",
                limit=self._settings.max_repo_size_mb,
                actual=int(size_mb),
            )

        result = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return result.stdout.decode().strip()

    def diff_paths(self, repo_path: Path, base_sha: str, head_sha: str) -> list[str]:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "diff", "--name-only", base_sha, head_sha],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return [line for line in result.stdout.decode().splitlines() if line]

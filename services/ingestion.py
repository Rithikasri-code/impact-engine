"""Ingestion service: orchestrates walking a repo and parsing every file it
finds, producing a single ParseResult that always accounts for every file —
either parsed, or explicitly recorded as unparsed with a reason."""
from __future__ import annotations

from pathlib import Path

from core.errors import UnsupportedLanguageError
from core.models import FileParseResult, Language, ParseResult, UnparsedFile, UnparsedReason
from core.ports import Parser, RepoWalker

_EXT_TO_LANGUAGE = {".py": Language.PYTHON, ".go": Language.GO}

_MAX_FILE_BYTES = 2_000_000  # 2MB — pathological generated files get skipped, not hung on


def ingest_repo(
    repo_root: Path,
    repo_id: str,
    commit_sha: str,
    walker: RepoWalker,
    parser: Parser,
) -> ParseResult:
    """Walk repo_root, parse every discovered file, and return a complete
    ParseResult. Never raises on a per-file failure — those are captured as
    UnparsedFile entries. Only walker-level security errors (path escape)
    propagate, since those indicate a hostile repo rather than a normal
    parse failure."""
    paths = walker.walk(repo_root)

    files: list[FileParseResult] = []
    unparsed: list[UnparsedFile] = []

    for abs_path in paths:
        rel_path = str(abs_path.relative_to(repo_root.resolve()))
        language = _EXT_TO_LANGUAGE.get(abs_path.suffix)

        if language is None:
            unparsed.append(
                UnparsedFile(
                    path=rel_path,
                    reason=UnparsedReason.UNSUPPORTED_LANGUAGE,
                    detail=f"extension {abs_path.suffix!r} not supported",
                )
            )
            continue

        try:
            size = abs_path.stat().st_size
        except OSError as exc:
            unparsed.append(
                UnparsedFile(path=rel_path, reason=UnparsedReason.READ_ERROR, detail=str(exc))
            )
            continue

        if size > _MAX_FILE_BYTES:
            unparsed.append(
                UnparsedFile(
                    path=rel_path,
                    reason=UnparsedReason.FILE_TOO_LARGE,
                    detail=f"{size} bytes exceeds {_MAX_FILE_BYTES}",
                )
            )
            continue

        try:
            source = abs_path.read_bytes()
        except OSError as exc:
            unparsed.append(
                UnparsedFile(path=rel_path, reason=UnparsedReason.READ_ERROR, detail=str(exc))
            )
            continue

        try:
            result = parser.parse_file(rel_path, source, language)
        except UnsupportedLanguageError as exc:
            unparsed.append(
                UnparsedFile(
                    path=rel_path, reason=UnparsedReason.UNSUPPORTED_LANGUAGE, detail=str(exc)
                )
            )
            continue
        except Exception as exc:  # tree-sitter itself doesn't raise on malformed
            # source (it produces ERROR nodes), but any unexpected failure in
            # our extraction logic is still recorded, not swallowed silently
            # and not allowed to abort the whole repo ingestion.
            unparsed.append(
                UnparsedFile(path=rel_path, reason=UnparsedReason.SYNTAX_ERROR, detail=str(exc))
            )
            continue

        files.append(result)

    return ParseResult(
        repo_id=repo_id,
        commit_sha=commit_sha,
        files=tuple(files),
        unparsed_files=tuple(unparsed),
    )

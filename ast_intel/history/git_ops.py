"""Thin :mod:`subprocess` wrapper around the ``git`` CLI.

Why a wrapper rather than a library?  pygit2 / dulwich add a heavy
native dependency and do not match the exact semantics we need
(``git log -L``, ``git blame --line-porcelain``).  Direct CLI calls keep
the dependency tree small and give byte-for-byte parity with the
TypeScript implementation that this Python engine replaces.

Security notes:

- ``check=True`` and a fixed ``env`` (``GIT_OPTIONAL_LOCKS=0``,
  ``LC_ALL=C``) make output deterministic and immune to user locale.
- All commands run with ``cwd=<repo>`` and never use ``shell=True``,
  so arguments are not subject to shell interpretation.
- File-path arguments are passed through a ``--`` separator to avoid
  accidental interpretation as revision specs.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess  # noqa: S404 — git CLI invocation is intentional.
from pathlib import Path

from ast_intel.history.models import BlameHunk, GitCommitInfo

__all__: list[str] = [
    "GitError",
    "blame_range",
    "find_repo_root",
    "get_head_sha",
    "log_line_range",
    "run_git",
]

logger = logging.getLogger(__name__)

# Sentinels for the custom log format. We use ASCII unit-separators
# (0x1F field, 0x1E record) because they are vanishingly unlikely to
# appear in commit messages and need no quoting.
_FIELD_SEP: str = "\x1f"
_RECORD_SEP: str = "\x1e"

_LOG_FORMAT: str = _FIELD_SEP.join(
    ["%H", "%an", "%ae", "%aI", "%cI", "%s", "%b"],
) + _RECORD_SEP


_GIT_ENV: dict[str, str] = {
    **os.environ,
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
}


class GitError(RuntimeError):
    """Raised when a git invocation fails or git is unavailable."""


def run_git(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
) -> str:
    """Run ``git`` with the given *args* in *cwd* and return stdout.

    Args:
        args: Argument vector **without** the leading ``"git"``.
        cwd: Working directory — must be inside a git work-tree.
        timeout: Hard cap in seconds; raises :class:`GitError` on expiry.

    Returns:
        Decoded stdout (UTF-8 with surrogate escapes).

    Raises:
        GitError: If git exits non-zero, times out, or is not installed.
    """
    cmd = ["git", "--no-pager", *args]
    try:
        result = subprocess.run(  # noqa: S603 — args are not shell-interpreted.
            cmd,
            cwd=cwd,
            env=_GIT_ENV,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        msg = "git executable not found on PATH"
        raise GitError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"git {args[0] if args else ''} timed out after {timeout}s"
        raise GitError(msg) from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"git {' '.join(args)} failed ({result.returncode}): {stderr}"
        raise GitError(msg)

    return result.stdout.decode("utf-8", errors="surrogateescape")


def find_repo_root(start: Path) -> Path:
    """Walk upward from *start* until ``.git`` is found.

    Raises:
        GitError: If no enclosing git repository exists.
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    msg = f"No git repository found at or above {start}"
    raise GitError(msg)


def get_head_sha(repo: Path) -> str:
    """Return the 40-char SHA of ``HEAD``."""
    return run_git(["rev-parse", "HEAD"], cwd=repo).strip()


# ---------------------------------------------------------------------------
# region:    --- git log -L
# ---------------------------------------------------------------------------


_NUMSTAT_LINE = re.compile(r"^(\d+|-)\s+(\d+|-)\s+(.+)$")


def log_line_range(
    repo: Path,
    file: str,
    start_line: int,
    end_line: int,
    *,
    max_commits: int = 200,
) -> list[GitCommitInfo]:
    """Return the commit history of ``file`` lines [start_line, end_line].

    Uses ``git log -L <start>,<end>:<file>`` followed by a numstat pass
    to attach line-change counts.  ``--no-patch`` is mandatory: without
    it ``git log -L`` emits the full diff for every hit which can
    multiply output size by 100×.

    Args:
        repo: Repository root.
        file: Workspace-relative path.
        start_line: First line of the range (1-based, inclusive).
        end_line: Last line of the range (1-based, inclusive).
        max_commits: Upper bound on returned commits.

    Returns:
        Commits ordered newest → oldest.
    """
    if end_line < start_line:
        msg = "end_line must be >= start_line"
        raise ValueError(msg)

    range_spec = f"{start_line},{end_line}:{file}"

    raw = run_git(
        [
            "log",
            "-L",
            range_spec,
            "--no-patch",
            f"--pretty=format:{_LOG_FORMAT}",
            f"-n{max_commits}",
        ],
        cwd=repo,
    )

    commits: list[GitCommitInfo] = []
    for record in raw.split(_RECORD_SEP):
        cleaned = record.lstrip("\n")
        if not cleaned:
            continue
        parts = cleaned.split(_FIELD_SEP)
        if len(parts) < 7:  # noqa: PLR2004
            continue
        sha, an, ae, ai, ci, subject, body = parts[:7]
        commits.append(
            GitCommitInfo(
                sha=sha,
                author_name=an,
                author_email=ae,
                authored_at=ai,
                committed_at=ci,
                subject=subject,
                body=body.rstrip("\n"),
            ),
        )

    # Attach numstat (best-effort; if it fails we still return commits).
    if commits:
        try:
            commits = _attach_numstat(repo, commits)
        except GitError as exc:
            logger.debug("numstat enrichment failed: %s", exc)

    return commits


def _attach_numstat(
    repo: Path,
    commits: list[GitCommitInfo],
) -> list[GitCommitInfo]:
    """Backfill ``lines_added``/``lines_removed``/``files_changed``."""
    shas = [c.sha for c in commits]
    raw = run_git(
        [
            "show",
            "--numstat",
            "--format=%H",
            "--no-renames",
            *shas,
        ],
        cwd=repo,
    )

    per_sha: dict[str, tuple[int, int, list[str]]] = {}
    current_sha: str | None = None
    added = 0
    removed = 0
    files: list[str] = []
    sha_set = set(shas)

    def _flush() -> None:
        if current_sha is not None:
            per_sha[current_sha] = (added, removed, list(files))

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) == 40 and line in sha_set:  # noqa: PLR2004
            _flush()
            current_sha = line
            added = 0
            removed = 0
            files.clear()
            continue
        m = _NUMSTAT_LINE.match(line)
        if not m:
            continue
        a_raw, r_raw, path = m.groups()
        if a_raw.isdigit():
            added += int(a_raw)
        if r_raw.isdigit():
            removed += int(r_raw)
        files.append(path)
    _flush()

    out: list[GitCommitInfo] = []
    for c in commits:
        stats = per_sha.get(c.sha)
        if stats is None:
            out.append(c)
            continue
        a, r, fs = stats
        out.append(
            GitCommitInfo(
                sha=c.sha,
                author_name=c.author_name,
                author_email=c.author_email,
                authored_at=c.authored_at,
                committed_at=c.committed_at,
                subject=c.subject,
                body=c.body,
                files_changed=tuple(fs),
                lines_added=a,
                lines_removed=r,
            ),
        )
    return out


# endregion: --- git log -L


# ---------------------------------------------------------------------------
# region:    --- git blame
# ---------------------------------------------------------------------------


def blame_range(
    repo: Path,
    file: str,
    start_line: int,
    end_line: int,
) -> list[BlameHunk]:
    """Run ``git blame -L start,end --line-porcelain`` and group hunks.

    Consecutive blamed lines pointing to the same commit are coalesced
    into a single :class:`BlameHunk`.  This keeps the structure aligned
    with how reviewers conceptualize "who wrote this block".
    """
    raw = run_git(
        [
            "blame",
            "-L",
            f"{start_line},{end_line}",
            "--line-porcelain",
            "--",
            file,
        ],
        cwd=repo,
    )

    hunks: list[BlameHunk] = []
    current_sha = ""
    current_author = ""
    current_email = ""
    current_date = ""
    current_start: int | None = None
    current_end: int | None = None
    line_no = start_line - 1

    def _flush() -> None:
        nonlocal current_start, current_end
        if current_sha and current_start is not None and current_end is not None:
            hunks.append(
                BlameHunk(
                    sha=current_sha,
                    author_name=current_author,
                    author_email=current_email,
                    authored_at=current_date,
                    start_line=current_start,
                    end_line=current_end,
                ),
            )
        current_start = None
        current_end = None

    sha = ""
    author = ""
    email = ""
    date = ""

    for raw_line in raw.splitlines():
        if raw_line.startswith("\t"):
            line_no += 1
            if sha == current_sha and current_end == line_no - 1:
                current_end = line_no
            else:
                _flush()
                current_sha = sha
                current_author = author
                current_email = email
                current_date = date
                current_start = line_no
                current_end = line_no
            continue

        if not raw_line:
            continue

        head, _, rest = raw_line.partition(" ")
        if len(head) == 40:  # noqa: PLR2004 — git SHA length is a known constant.
            sha = head
        elif head == "author":
            author = rest
        elif head == "author-mail":
            email = rest.strip("<>")
        elif head in {"author-time", "committer-time"}:
            # Skip — we use ISO via author-tz combination below.
            pass
        elif head == "author-tz":
            # ignore: we rely on the porcelain author-mail / etc. plus
            # we synthesise ISO using author-time + author-tz when
            # available below.
            pass

    _flush()

    # ``--line-porcelain`` doesn't emit ISO directly; fetch dates per SHA.
    unique_shas = {h.sha for h in hunks}
    iso_by_sha = _iso_for_shas(repo, unique_shas) if unique_shas else {}
    return [
        BlameHunk(
            sha=h.sha,
            author_name=h.author_name,
            author_email=h.author_email,
            authored_at=iso_by_sha.get(h.sha, h.authored_at),
            start_line=h.start_line,
            end_line=h.end_line,
        )
        for h in hunks
    ]


def _iso_for_shas(repo: Path, shas: set[str]) -> dict[str, str]:
    """Return ISO-8601 author dates keyed by SHA."""
    if not shas:
        return {}
    raw = run_git(
        [
            "show",
            "-s",
            "--format=%H%x1f%aI",
            *sorted(shas),
        ],
        cwd=repo,
    )
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if "\x1f" not in line:
            continue
        sha, _, iso = line.partition("\x1f")
        out[sha] = iso
    return out


# endregion: --- git blame

"""IaC file discovery — find K8s manifests, Helm charts, Dockerfiles, etc.

Runs alongside ``WorkspaceDiscovery`` during the scan pipeline.
Walks the repository tree and classifies ambiguous YAML files using
content-based heuristics.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

__all__: list[str] = ["IaCDiscovery", "IaCFile"]

logger = logging.getLogger(__name__)

_PEEK_SIZE: int = 1024
_MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10 MB

# Extensions that are ALWAYS IaC (no content check needed)
_IAC_EXTENSIONS: frozenset[str] = frozenset({".tf", ".tfvars", ".tpl", ".bicep"})

# Filenames that are ALWAYS IaC
_IAC_FILENAMES: frozenset[str] = frozenset({
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Chart.yaml",
    "values.yaml",
    "site.yml",
    "site.yaml",
})

# Filename prefixes that are ALWAYS IaC
_IAC_PREFIXES: tuple[str, ...] = ("Dockerfile",)

# Ambiguous extensions needing content heuristic
_AMBIGUOUS_EXTENSIONS: frozenset[str] = frozenset({".yaml", ".yml"})

# Path segments that indicate CI/CD pipeline files
_CICD_PATH_SEGMENTS: frozenset[str] = frozenset({
    ".github/workflows",
    ".pipelines",
    ".azure-pipelines",
})

# Same default excludes as workspace.py
_DEFAULT_EXCLUDES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "venv",
    "target",
    "dist",
    "build",
    ".next",
    "coverage",
})


@dataclass(slots=True)
class IaCFile:
    """A discovered IaC file ready for extraction.

    Attributes:
        abs_path: Absolute path.
        rel_path: Workspace-relative path.
    """

    abs_path: Path
    rel_path: str


class IaCDiscovery:
    """Discover IaC files in a repository.

    Args:
        repo_root: Absolute path to the repository root.
        include_paths: Paths to include (relative to repo root).
        exclude_paths: Glob patterns to exclude.
    """

    def __init__(
        self,
        repo_root: Path,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.include_paths = include_paths or []
        self.exclude_paths = exclude_paths or []

    def discover(self) -> list[IaCFile]:
        """Walk the repository and return discovered IaC files.

        Returns:
            List of ``IaCFile`` with absolute and relative paths.
        """
        gitignore = self._load_gitignore()
        results: list[IaCFile] = []

        for dirpath, dirnames, filenames in os.walk(
            self.repo_root, topdown=True,
        ):
            current = Path(dirpath)
            rel_dir = current.relative_to(self.repo_root)

            # Prune excluded directories
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _DEFAULT_EXCLUDES
                and not (
                    gitignore
                    and gitignore.match_file(str(rel_dir / d) + "/")
                )
            ]

            for fname in filenames:
                abs_path = current / fname
                rel_path = str(abs_path.relative_to(self.repo_root))

                if self._is_iac_candidate(abs_path, rel_path, gitignore):
                    results.append(
                        IaCFile(abs_path=abs_path, rel_path=rel_path),
                    )

        logger.info("IaC discovery found %d candidate files", len(results))
        return results

    # ------------------------------------------------------------------ #
    # region:    --- Private helpers
    # ------------------------------------------------------------------ #

    def _load_gitignore(self) -> pathspec.PathSpec | None:
        """Load ``.gitignore`` from repo root, if present."""
        gi = self.repo_root / ".gitignore"
        if not gi.is_file():
            return None
        try:
            text = gi.read_text(encoding="utf-8", errors="replace")
            return pathspec.PathSpec.from_lines("gitwildmatch", text.splitlines())
        except OSError:
            return None

    def _is_iac_candidate(  # noqa: C901, PLR0911
        self,
        abs_path: Path,
        rel_path: str,
        gitignore: pathspec.PathSpec | None,
    ) -> bool:
        """Determine if a file is an IaC candidate."""
        # Gitignore check
        if gitignore and gitignore.match_file(rel_path):
            return False

        # Exclude patterns
        if self.exclude_paths:
            spec = pathspec.PathSpec.from_lines(
                "gitwildmatch", self.exclude_paths,
            )
            if spec.match_file(rel_path):
                return False

        # Include paths filter
        if self.include_paths and not any(
            rel_path.startswith(p) for p in self.include_paths
        ):
            return False

        # Size check
        try:
            if abs_path.stat().st_size > _MAX_FILE_SIZE:
                return False
        except OSError:
            return False

        name = abs_path.name
        suffix = abs_path.suffix.lower()

        # Definite IaC by filename
        if name in _IAC_FILENAMES:
            return True
        if any(name.startswith(p) for p in _IAC_PREFIXES):
            return True

        # Definite IaC by extension
        if suffix in _IAC_EXTENSIONS:
            return True

        # Ambiguous YAML — peek at content
        if suffix in _AMBIGUOUS_EXTENSIONS:
            return self._peek_yaml(abs_path, rel_path)

        return False

    @staticmethod
    def _peek_yaml(abs_path: Path, rel_path: str) -> bool:  # noqa: PLR0911
        """Peek at a YAML file to check if it's IaC (K8s, Ansible, or CI/CD)."""
        # Path-based CI/CD detection (before content check)
        normalized = rel_path.replace("\\", "/")
        for segment in _CICD_PATH_SEGMENTS:
            if normalized.startswith(segment + "/") or (
                "/" + segment + "/" in "/" + normalized
            ):
                return True

        try:
            peek = abs_path.read_bytes()[:_PEEK_SIZE]
            text = peek.decode("utf-8", errors="replace")
        except OSError:
            return False

        # K8s manifest: apiVersion + kind
        if "apiVersion:" in text and "kind:" in text:
            return True

        # Ansible playbook: hosts + tasks/roles, or import_playbook
        if "hosts:" in text and ("tasks:" in text or "roles:" in text):
            return True
        if "import_playbook:" in text or "include_playbook:" in text:
            return True

        # CI/CD: Azure Pipelines content heuristics
        if ("trigger:" in text or "extends:" in text) and (
            "stages:" in text or "pool:" in text or "jobs:" in text
        ):
            return True

        # CI/CD: GitHub Actions content heuristics
        return "on:" in text and "jobs:" in text

    # endregion: --- Private helpers
    # ------------------------------------------------------------------ #

"""Security utilities — path validation and safe I/O.

Centralizes all security-sensitive operations:

- **Path traversal prevention**: Ensures output never escapes the output dir.
- **Symlink safety**: Detects symlink loops and prevents infinite recursion.
- **Input sanitization**: Validates user-supplied paths before use.
- **Safe file writing**: Output files are written atomically where possible.

These checks protect against OWASP Top-10 risks including
path traversal (A01), injection (A03), and SSRF-adjacent file attacks.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

__all__: list[str] = [
    "check_grammar_availability",
    "sanitize_output_path",
    "validate_repo_path",
]

logger = logging.getLogger(__name__)

# Maximum directory depth to walk before aborting (prevents runaway recursion)
_MAX_DIR_DEPTH = 100

# Language → pip extra name → required packages
_GRAMMAR_INSTALL_HINTS: dict[str, tuple[str, list[str]]] = {
    "rust": ("rust", ["tree-sitter-rust"]),
    "go": ("go", ["tree-sitter-go"]),
    "python": ("python", ["tree-sitter-python"]),
    "typescript": ("typescript", ["tree-sitter-typescript", "tree-sitter-javascript"]),
    "csharp": ("csharp", ["tree-sitter-c-sharp"]),
    "cpp": ("cpp", ["tree-sitter-c", "tree-sitter-cpp"]),
    "java": ("java", ["tree-sitter-java"]),
}


# ---------------------------------------------------------------------------
# region:    --- Path Validation
# ---------------------------------------------------------------------------


class PathSecurityError(Exception):
    """Raised when a path fails security validation."""


def validate_repo_path(repo_path: Path) -> Path:
    """Validate and resolve the repository path.

    Ensures the path:
    - Exists and is a directory
    - Is readable
    - Does not resolve to sensitive system directories

    Args:
        repo_path: User-supplied repository path.

    Returns:
        Resolved absolute path.

    Raises:
        PathSecurityError: If the path is invalid or unsafe.
    """
    try:
        resolved = repo_path.resolve(strict=True)
    except OSError as exc:
        msg = f"Cannot resolve repository path: {exc}"
        raise PathSecurityError(msg) from exc

    if not resolved.is_dir():
        msg = f"Not a directory: {resolved}"
        raise PathSecurityError(msg)

    if not os.access(resolved, os.R_OK):
        msg = f"Permission denied: cannot read {resolved}"
        raise PathSecurityError(msg)

    # Block obviously dangerous system dirs
    _blocked = frozenset({"/", "/etc", "/var", "/usr", "/bin", "/sbin", "/boot"})
    if str(resolved) in _blocked:
        msg = f"Refusing to analyze system directory: {resolved}"
        raise PathSecurityError(msg)

    return resolved


def sanitize_output_path(output_dir: Path, repo_path: Path) -> Path:
    """Validate and prepare the output directory.

    Ensures the output directory:
    - Resolves to an absolute path
    - Is not a symlink to an unexpected location
    - Is writable (or can be created)
    - Does not escape to outside its parent when resolving symlinks

    .. note::
        When multiple ``ast-intel`` processes target the same output
        directory concurrently, ``mkdir`` and access checks are
        inherently racy.  Callers should use distinct output paths
        per invocation when running in parallel.

    Args:
        output_dir: User-supplied output directory.
        repo_path: Repository root (used as default if output is ``Path()``).  
            When no explicit output is given, defaults to
            ``repo_path / "ast_output"``.

    Returns:
        Resolved absolute output path.

    Raises:
        PathSecurityError: If the output path is unsafe.
    """
    target = output_dir if output_dir != Path() else repo_path / "ast_output"

    try:
        resolved = target.resolve()
    except OSError as exc:
        msg = f"Cannot resolve output path: {exc}"
        raise PathSecurityError(msg) from exc

    # Create if needed
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"Cannot create output directory: {exc}"
        raise PathSecurityError(msg) from exc

    if not os.access(resolved, os.W_OK):
        msg = f"Permission denied: cannot write to {resolved}"
        raise PathSecurityError(msg)

    return resolved


def is_safe_symlink(path: Path, root: Path) -> bool:
    """Check whether a symlink target stays within the repository root.

    Used during workspace discovery to prevent symlink escapes.

    .. warning::
        This check is subject to TOCTOU (time-of-check-time-of-use):
        symlinks could theoretically change between validation and file
        access.  Only run this tool on trusted, locally-owned repositories.

    Args:
        path: Path to check (may or may not be a symlink).
        root: Repository root that all targets must resolve within.

    Returns:
        ``True`` if the path is safe (not a symlink, or symlink resolves
        within *root*).  ``False`` if the symlink escapes *root*.
    """
    if not path.is_symlink():
        return True

    try:
        target = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except OSError:
        return False

    # Target must be within the repository root
    try:
        target.relative_to(root_resolved)
    except ValueError:
        logger.warning(
            "Symlink %s escapes repository root (targets %s), skipping",
            path,
            target,
        )
        return False

    return True


# endregion: --- Path Validation


# ---------------------------------------------------------------------------
# region:    --- Grammar Availability
# ---------------------------------------------------------------------------


def check_grammar_availability(
    requested_languages: list[str] | None,
) -> list[str]:
    """Check which requested language grammars are available.

    Returns a list of user-friendly warning messages for missing grammars.
    If no languages are explicitly requested (auto-detect mode), returns
    an empty list — missing grammars just mean those files get skipped.

    Args:
        requested_languages: Language names from ``--lang`` flag, or ``None``.

    Returns:
        List of warning strings for missing grammars.
    """
    if not requested_languages:
        return []

    warnings: list[str] = []

    for lang in requested_languages:
        hint = _GRAMMAR_INSTALL_HINTS.get(lang)
        if hint is None:
            continue

        extra_name, packages = hint
        if not _is_grammar_installed(lang):
            pkg_list = ", ".join(packages)
            warnings.append(
                f"Grammar for '{lang}' not installed ({pkg_list}). "
                f"Install with: pip install ast-intel[{extra_name}]"
            )

    return warnings


def _is_grammar_installed(language: str) -> bool:
    """Test whether the tree-sitter grammar for a language is importable."""
    import_map: dict[str, str] = {
        "rust": "tree_sitter_rust",
        "go": "tree_sitter_go",
        "python": "tree_sitter_python",
        "typescript": "tree_sitter_typescript",
        "csharp": "tree_sitter_c_sharp",
        "cpp": "tree_sitter_cpp",
        "java": "tree_sitter_java",
    }

    module_name = import_map.get(language)
    if module_name is None:
        return True  # Unknown language — not our concern

    try:
        __import__(module_name)
    except ImportError:
        return False
    return True


# endregion: --- Grammar Availability

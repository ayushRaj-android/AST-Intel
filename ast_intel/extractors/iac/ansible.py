"""Ansible extractor — parse Ansible playbook YAML files.

Detects playbooks by the presence of ``hosts:`` + ``tasks:`` (or
``roles:``) keys and the absence of ``apiVersion:`` (which flags K8s).
Jinja2 template expressions (``{{ }}``) are stored verbatim
as string properties — they are NOT rendered.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

import yaml

from ast_intel.extractors.iac_base import IaCExtractorBase
from ast_intel.models.ast_node import Span
from ast_intel.models.iac_model import (
    IaCContext,
    IaCEdge,
    IaCGraph,
    IaCResource,
)

__all__: list[str] = ["AnsibleExtractor"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _compute_doc_line_offsets(text: str) -> list[int]:
    """Return the 1-based start line of each YAML document."""
    offsets: list[int] = [1]
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "---":
            offsets.append(line_no + 1)
    return offsets


def _span_for_doc(
    line_offsets: list[int],
    doc_idx: int,
    total_lines: int,
) -> Span | None:
    """Build a Span for the doc_idx-th YAML document."""
    if doc_idx >= len(line_offsets):
        return None
    start = line_offsets[doc_idx]
    if doc_idx + 1 < len(line_offsets):
        end = max(line_offsets[doc_idx + 1] - 1, start)
    else:
        end = total_lines
    return Span(start_line=start, start_col=0, end_line=end, end_col=0)


def _truncate(value: object, max_len: int = 100) -> str:
    """Stringify and truncate a value for properties."""
    s = str(value)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _safe_str(value: object) -> str:
    """Convert a value to string, handling None."""
    if value is None:
        return ""
    return str(value)


# endregion: --- Helpers
# ---------------------------------------------------------------------------


class AnsibleExtractor(IaCExtractorBase):
    """Parse Ansible playbook YAML files into IaC resources."""

    format_id: ClassVar[str] = "ansible"
    file_patterns: ClassVar[list[str]] = ["**/*.yml", "**/*.yaml"]
    file_extensions: ClassVar[list[str]] = [".yaml", ".yml"]

    # ------------------------------------------------------------------ #
    # region:    --- Detection
    # ------------------------------------------------------------------ #

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:  # noqa: PLR0911
        """Detect Ansible playbooks by structural heuristics.

        A YAML file is an Ansible playbook if it:
        - Is a list of plays (each with ``hosts:`` or ``import_playbook:``)
        - Does NOT have ``apiVersion:`` (that's Kubernetes)
        - Does NOT have ``{{ }}`` without ``hosts:`` (that's Helm template)
        """
        try:
            text = source_peek.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False

        # Reject K8s manifests
        if "apiVersion:" in text and "kind:" in text:
            return False

        # Reject Helm Chart.yaml
        if file_path.name == "Chart.yaml":
            return False

        # Ansible playbook indicators
        has_hosts = "hosts:" in text
        has_import_playbook = "import_playbook:" in text
        has_include_playbook = "include_playbook:" in text
        has_tasks = "tasks:" in text
        has_handlers = "handlers:" in text

        # A playbook file must have hosts or import_playbook
        if has_hosts or has_import_playbook or has_include_playbook:
            return True

        # A tasks-only file (included via include_tasks) in a playbooks/ dir
        if has_tasks and "playbook" in str(file_path).lower():
            return True

        # A file with handlers in a playbooks/ directory
        if has_handlers and "playbook" in str(file_path).lower():
            return True

        # Role main.yml detection
        return bool(has_tasks and "roles/" in str(file_path))

    # endregion: --- Detection
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Extraction
    # ------------------------------------------------------------------ #

    def extract(
        self,
        file_path: Path,  # noqa: ARG002
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse an Ansible YAML file into an IaCGraph."""
        graph = IaCGraph(file=context.rel_path)
        text = source.decode("utf-8", errors="replace")
        total_lines = text.count("\n") + 1
        line_offsets = _compute_doc_line_offsets(text)

        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            graph.errors.append(f"YAML parse error: {exc}")
            return graph

        for doc_idx, doc in enumerate(docs):
            if doc is None:
                continue

            span = _span_for_doc(line_offsets, doc_idx, total_lines)

            # Top-level list -> list of plays (standard playbook)
            if isinstance(doc, list):
                for play_idx, play in enumerate(doc):
                    if not isinstance(play, dict):
                        continue
                    self._parse_play(
                        play, context.rel_path, graph, span, play_idx,
                    )
            # Top-level dict -> single play (uncommon but valid)
            elif isinstance(doc, dict):
                self._parse_play(
                    doc, context.rel_path, graph, span, 0,
                )

        return graph

    # endregion: --- Extraction
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Play parsing
    # ------------------------------------------------------------------ #

    def _parse_play(  # noqa: C901, PLR0912
        self,
        play: dict[str, object],
        rel_path: str,
        graph: IaCGraph,
        span: Span | None,
        play_idx: int,
    ) -> None:
        """Parse a single Ansible play dict into resources and edges."""
        # --- import_playbook shortcut ---
        if "import_playbook" in play:
            imported = _safe_str(play["import_playbook"])
            # Create a minimal playbook resource for the importer
            importer_name = Path(rel_path).stem
            graph.edges.append(
                IaCEdge(
                    source_name=importer_name,
                    target_name=Path(imported).stem,
                    relation="imports_playbook",
                ),
            )
            return

        # --- Full play with hosts ---
        play_name = _safe_str(
            play.get("name", f"play-{play_idx}"),
        )
        hosts = _safe_str(play.get("hosts", ""))
        become = play.get("become", False)
        gather_facts = play.get("gather_facts")

        props: dict[str, str] = {}
        if hosts:
            props["hosts"] = hosts
        if become:
            props["become"] = str(become)
        if gather_facts is not None:
            props["gather_facts"] = str(gather_facts)

        # Collect vars keys (not values — may contain secrets)
        play_vars = play.get("vars", {})
        if isinstance(play_vars, dict) and play_vars:
            props["vars"] = ",".join(sorted(str(k) for k in play_vars))

        playbook_resource = IaCResource(
            kind="AnsiblePlaybook",
            name=play_name,
            file=rel_path,
            span=span,
            properties=props,
        )
        graph.resources.append(playbook_resource)

        # --- Parse tasks ---
        for section in ("pre_tasks", "tasks", "post_tasks"):
            tasks = play.get(section, [])
            if not isinstance(tasks, list):
                continue
            for task_idx, task in enumerate(tasks):
                if not isinstance(task, dict):
                    continue
                self._parse_task(
                    task, rel_path, graph, playbook_resource.name,
                    section, task_idx,
                )

        # --- Parse roles ---
        roles = play.get("roles", [])
        if isinstance(roles, list):
            for role in roles:
                role_name = self._extract_role_name(role)
                if role_name:
                    role_resource = IaCResource(
                        kind="AnsibleRole",
                        name=role_name,
                        file=rel_path,
                        span=span,
                        properties={},
                    )
                    graph.resources.append(role_resource)
                    graph.edges.append(
                        IaCEdge(
                            source_name=playbook_resource.name,
                            target_name=role_name,
                            relation="uses_role",
                        ),
                    )

        # --- Parse handlers ---
        handlers = play.get("handlers", [])
        if isinstance(handlers, list):
            for handler_idx, handler in enumerate(handlers):
                if not isinstance(handler, dict):
                    continue
                self._parse_task(
                    handler, rel_path, graph, playbook_resource.name,
                    "handlers", handler_idx,
                )

    # endregion: --- Play parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Task parsing
    # ------------------------------------------------------------------ #

    def _parse_task(  # noqa: PLR0913
        self,
        task: dict[str, object],
        rel_path: str,
        graph: IaCGraph,
        playbook_name: str,
        section: str,
        task_idx: int,
    ) -> None:
        """Parse a single task into an ANSIBLE_TASK resource + edges."""
        task_name = _safe_str(
            task.get("name", f"task-{section}-{task_idx}"),
        )

        # Determine the module being used
        module_name = self._detect_module(task)

        props: dict[str, str] = {
            "section": section,
        }
        if module_name:
            props["module"] = module_name

        # when condition
        when = task.get("when")
        if when is not None:
            props["when"] = _truncate(when)

        # loop
        loop_val = task.get("loop")
        if loop_val is not None:
            props["has_loop"] = "true"

        # register
        register = task.get("register")
        if register:
            props["register"] = _safe_str(register)

        # Detect include_tasks or import_tasks directives
        include_tasks = task.get("include_tasks") or task.get(
            "ansible.builtin.include_tasks",
        )
        if include_tasks:
            props["include_tasks"] = _safe_str(include_tasks)

        task_resource = IaCResource(
            kind="AnsibleTask",
            name=task_name,
            file=rel_path,
            properties=props,
        )
        graph.resources.append(task_resource)

        # RUNS_TASK edge: playbook -> task
        graph.edges.append(
            IaCEdge(
                source_name=playbook_name,
                target_name=task_name,
                relation="runs_task",
            ),
        )

        # NOTIFIES_HANDLER edge: task -> handler (by name)
        notify = task.get("notify")
        if notify:
            if isinstance(notify, str):
                notify = [notify]
            if isinstance(notify, list):
                for handler_name in notify:
                    graph.edges.append(
                        IaCEdge(
                            source_name=task_name,
                            target_name=_safe_str(handler_name),
                            relation="notifies_handler",
                        ),
                    )

    # endregion: --- Task parsing
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Module detection
    # ------------------------------------------------------------------ #

    # Known Ansible module keywords — task dict keys that are NOT modules
    _NON_MODULE_KEYS: ClassVar[frozenset[str]] = frozenset({
        "name", "when", "loop", "loop_control", "register",
        "become", "become_user", "become_method",
        "notify", "tags", "vars", "environment",
        "changed_when", "failed_when", "ignore_errors",
        "no_log", "run_once", "delegate_to", "retries",
        "delay", "until", "timeout", "block", "rescue",
        "always", "listen", "check_mode", "diff",
        "any_errors_fatal", "throttle", "collections",
        "module_defaults", "connection", "async", "poll",
    })

    def _detect_module(self, task: dict[str, object]) -> str:
        """Detect the Ansible module name from a task dict.

        The module is the first key that isn't a task-level directive.
        Handles both short form (``command: ls``) and FQCN
        (``ansible.builtin.command``).
        """
        # Special directives that look like modules
        for directive in (
            "import_tasks", "include_tasks", "import_role",
            "include_role", "meta",
            "ansible.builtin.import_tasks",
            "ansible.builtin.include_tasks",
            "ansible.builtin.import_role",
            "ansible.builtin.include_role",
            "ansible.builtin.meta",
        ):
            if directive in task:
                return directive

        for key in task:
            if str(key) not in self._NON_MODULE_KEYS:
                return str(key)
        return ""

    @staticmethod
    def _extract_role_name(role: object) -> str:
        """Extract role name from a roles: list entry.

        Roles can be specified as a string or a dict with ``role:`` key.
        """
        if isinstance(role, str):
            return role
        if isinstance(role, dict):
            return _safe_str(role.get("role", role.get("name", "")))
        return ""

    # endregion: --- Module detection
    # ------------------------------------------------------------------ #

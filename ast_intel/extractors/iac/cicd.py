"""CI/CD pipeline extractor — Azure Pipelines and GitHub Actions YAML.

Detects pipelines by path patterns (``.pipelines/``, ``.github/workflows/``)
and content heuristics (``trigger:`` + ``stages:``, ``on:`` + ``jobs:``).

Supports:
- Azure Pipelines: stages → jobs → steps (including ``extends:`` pattern)
- GitHub Actions: jobs → steps (no explicit stages)
- Template references: ``- template: path@self`` → USES_TEMPLATE edges
- Pipeline triggers: ``resources.pipelines`` → TRIGGERS edges
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ast_intel.extractors.iac_base import IaCExtractorBase
from ast_intel.models.ast_node import Span
from ast_intel.models.iac_model import (
    IaCContext,
    IaCEdge,
    IaCGraph,
    IaCResource,
)

__all__: list[str] = ["CICDExtractor"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# region:    --- Constants
# ---------------------------------------------------------------------------

_CICD_PATH_SEGMENTS: frozenset[str] = frozenset({
    ".github/workflows",
    ".pipelines",
    ".azure-pipelines",
})

# Azure Pipelines: need at least one trigger-like key AND one structural key
_AZURE_TRIGGER_KEYS: frozenset[str] = frozenset({
    "trigger", "pr", "schedules", "extends", "resources",
})
_AZURE_STRUCTURE_KEYS: frozenset[str] = frozenset({
    "stages", "jobs", "steps", "pool",
})

_TRUNCATE_LEN: int = 60

# endregion: --- Constants
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _truncate(value: object, max_len: int = _TRUNCATE_LEN) -> str:
    """Stringify and truncate a value for step names / properties."""
    s = str(value)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _safe_str(value: object) -> str:
    """Convert a value to string, handling None."""
    if value is None:
        return ""
    return str(value)


def _first_line(text: str) -> str:
    """Return the first non-empty line of text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return text.strip()


def _compute_line_offsets(text: str) -> list[int]:
    """Return cumulative byte offsets for each line (0-indexed)."""
    offsets: list[int] = [0]
    for line in text.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


# endregion: --- Helpers
# ---------------------------------------------------------------------------


class CICDExtractor(IaCExtractorBase):
    """Extract CI/CD pipeline structure from Azure Pipelines and GitHub Actions."""

    format_id: ClassVar[str] = "cicd"
    file_patterns: ClassVar[list[str]] = ["**/*.yml", "**/*.yaml"]
    file_extensions: ClassVar[list[str]] = [".yaml", ".yml"]

    # ------------------------------------------------------------------ #
    # region:    --- Detection
    # ------------------------------------------------------------------ #

    def can_handle(self, file_path: Path, source_peek: bytes) -> bool:  # noqa: PLR0911, C901
        """Detect CI/CD pipeline files by path and content heuristics."""
        name = file_path.name
        suffix = file_path.suffix.lower()
        if suffix not in (".yml", ".yaml"):
            return False

        # Reject K8s manifests and Helm charts
        if name == "Chart.yaml":
            return False

        try:
            text = source_peek.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return False

        if "apiVersion:" in text and "kind:" in text:
            return False

        # Path-based detection
        normalized = str(file_path).replace("\\", "/")
        for segment in _CICD_PATH_SEGMENTS:
            if f"/{segment}/" in normalized or normalized.startswith(
                f"{segment}/",
            ):
                return True

        # Content-based detection
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            return False
        if not isinstance(doc, dict):
            return False

        keys = set(doc.keys())
        if self._is_github_actions(keys):
            return True
        if self._is_azure_pipelines(keys):
            return True
        return self._is_azure_extends(doc)

    @staticmethod
    def _is_azure_pipelines(keys: set[object]) -> bool:
        """Azure Pipelines: need trigger-like AND structural keys.

        Also detects ``extends:`` with nested ``stages:``.
        """
        str_keys = {str(k) for k in keys}
        return bool(
            str_keys & {"trigger", "pr", "schedules", "extends", "resources"}
            and str_keys & {"stages", "jobs", "steps", "pool"},
        )

    @staticmethod
    def _is_azure_extends(doc: dict[str, object]) -> bool:
        """Check for ``extends.parameters.stages`` pattern."""
        extends = doc.get("extends")
        if not isinstance(extends, dict):
            return False
        params = extends.get("parameters")
        return isinstance(params, dict) and "stages" in params

    @staticmethod
    def _is_github_actions(keys: set[object]) -> bool:
        """GitHub Actions: must have ``on`` and ``jobs``.

        Note: YAML parses ``on:`` as boolean ``True``.
        """
        return (True in keys or "on" in keys) and "jobs" in keys

    # endregion: --- Detection
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Extraction entry point
    # ------------------------------------------------------------------ #

    def extract(
        self,
        file_path: Path,  # noqa: ARG002
        source: bytes,
        context: IaCContext,
    ) -> IaCGraph:
        """Parse a CI/CD YAML file into an IaCGraph."""
        graph = IaCGraph(file=context.rel_path)
        text = source.decode("utf-8", errors="replace")
        total_lines = text.count("\n") + 1

        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            graph.errors.append(f"YAML parse error: {exc}")
            return graph

        if not isinstance(doc, dict):
            graph.errors.append("CI/CD file is not a YAML mapping")
            return graph

        keys = set(doc.keys())

        if self._is_github_actions(keys):
            self._extract_github_actions(
                graph, context.rel_path, doc, total_lines,
            )
        else:
            # Default to Azure Pipelines for path-detected files
            self._extract_azure_pipelines(
                graph, context.rel_path, doc, total_lines,
            )

        return graph

    # endregion: --- Extraction entry point
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Azure Pipelines
    # ------------------------------------------------------------------ #

    def _extract_azure_pipelines(  # noqa: C901, PLR0912, PLR0915
        self,
        graph: IaCGraph,
        rel_path: str,
        doc: dict[str, Any],
        total_lines: int,
    ) -> None:
        """Extract Azure Pipelines hierarchy: pipeline → stages → jobs → steps."""
        # Pipeline resource
        pipeline_name = _safe_str(doc.get("name")) or Path(rel_path).stem
        pipeline_res = IaCResource(
            kind="CI_PIPELINE",
            name=pipeline_name,
            file=rel_path,
            span=Span(start_line=1, start_col=0, end_line=total_lines, end_col=0),
            properties={
                "platform": "azure_pipelines",
                "trigger": _truncate(doc.get("trigger", "")),
            },
        )
        # Variables
        variables = doc.get("variables")
        if isinstance(variables, dict):
            pipeline_res.properties["variables"] = _truncate(
                ",".join(f"{k}={v}" for k, v in variables.items()),
                200,
            )
        # Parameters
        params = doc.get("parameters")
        if isinstance(params, list):
            param_names = [
                _safe_str(p.get("name", ""))
                for p in params
                if isinstance(p, dict)
            ]
            pipeline_res.properties["parameters"] = ",".join(param_names)

        graph.resources.append(pipeline_res)

        # Pipeline trigger from resources.pipelines
        resource_pipelines = (doc.get("resources") or {})
        if isinstance(resource_pipelines, dict):
            resource_pipelines = resource_pipelines.get("pipelines", [])
        else:
            resource_pipelines = []
        for rp in resource_pipelines:
            if isinstance(rp, dict):
                source_name = _safe_str(rp.get("source", rp.get("pipeline", "")))
                if source_name:
                    graph.edges.append(IaCEdge(
                        source_name=source_name,
                        target_name=pipeline_name,
                        relation="triggers",
                    ))

        # Find stages: top-level or under extends.parameters
        stages = self._find_azure_stages(doc)

        # extends: template edge
        extends = doc.get("extends")
        if isinstance(extends, dict):
            ext_template = extends.get("template", "")
            if ext_template:
                ext_name = f"ext:{ext_template}"
                ext_res = IaCResource(
                    kind="CI_STEP",
                    name=ext_name,
                    file=rel_path,
                    properties={
                        "type": "extends_template",
                        "template_ref": str(ext_template),
                        "external": "true",
                    },
                )
                graph.resources.append(ext_res)
                graph.edges.append(IaCEdge(
                    source_name=pipeline_name,
                    target_name=ext_name,
                    relation="uses_template",
                ))

        if not stages:
            # Some template files have just steps: or jobs: at top level
            jobs = doc.get("jobs")
            if isinstance(jobs, list):
                self._parse_azure_jobs(
                    graph, rel_path, jobs, pipeline_name,
                )
            steps = doc.get("steps")
            if isinstance(steps, list):
                self._parse_azure_steps(
                    graph, rel_path, steps, pipeline_name,
                )
            return

        for stage_dict in stages:
            if not isinstance(stage_dict, dict):
                continue
            stage_name = _safe_str(
                stage_dict.get("stage", stage_dict.get("displayName", "")),
            )
            if not stage_name:
                stage_name = f"stage_{stages.index(stage_dict)}"

            stage_res = IaCResource(
                kind="CI_STAGE",
                name=stage_name,
                file=rel_path,
                properties={},
            )
            display_name = stage_dict.get("displayName")
            if display_name:
                stage_res.properties["displayName"] = str(display_name)

            variables = stage_dict.get("variables")
            if isinstance(variables, dict):
                stage_res.properties["variables"] = _truncate(
                    ",".join(f"{k}={v}" for k, v in variables.items()),
                    200,
                )

            graph.resources.append(stage_res)
            graph.edges.append(IaCEdge(
                source_name=pipeline_name,
                target_name=stage_name,
                relation="contains",
            ))

            # Jobs within stage
            jobs = stage_dict.get("jobs", [])
            if isinstance(jobs, list):
                self._parse_azure_jobs(graph, rel_path, jobs, stage_name)

    @staticmethod
    def _find_azure_stages(doc: dict[str, Any]) -> list[Any]:
        """Find stages: top-level ``stages:`` or ``extends.parameters.stages``."""
        stages = doc.get("stages")
        if isinstance(stages, list):
            return stages

        extends = doc.get("extends")
        if isinstance(extends, dict):
            params = extends.get("parameters")
            if isinstance(params, dict):
                stages = params.get("stages")
                if isinstance(stages, list):
                    return stages
        return []

    def _parse_azure_jobs(  # noqa: C901
        self,
        graph: IaCGraph,
        rel_path: str,
        jobs: list[Any],
        parent_name: str,
    ) -> None:
        """Parse Azure Pipelines jobs within a stage."""
        seen_names: set[str] = set()
        for job_dict in jobs:
            if not isinstance(job_dict, dict):
                continue

            # Template job reference
            template_ref = job_dict.get("template")
            if template_ref:
                job_name = self._unique_name(
                    f"tmpl:{Path(str(template_ref).split('@', 1)[0]).stem}",
                    seen_names,
                )
                job_res = IaCResource(
                    kind="CI_JOB",
                    name=job_name,
                    file=rel_path,
                    properties={
                        "type": "template",
                        "template_ref": str(template_ref),
                    },
                )
                # Store template parameters
                params = job_dict.get("parameters")
                if isinstance(params, dict):
                    for k, v in params.items():
                        job_res.properties[f"param_{k}"] = _truncate(v)

                graph.resources.append(job_res)
                graph.edges.append(IaCEdge(
                    source_name=parent_name,
                    target_name=job_name,
                    relation="contains",
                ))
                graph.edges.append(IaCEdge(
                    source_name=parent_name,
                    target_name=job_name,
                    relation="uses_template",
                ))
                continue

            job_name = self._unique_name(
                _safe_str(
                    job_dict.get("job", job_dict.get("displayName", "")),
                ),
                seen_names,
            )
            if not job_name:
                job_name = self._unique_name("unnamed_job", seen_names)

            job_props: dict[str, str] = {}
            pool = job_dict.get("pool")
            if isinstance(pool, dict):
                job_props["pool_type"] = _safe_str(pool.get("type", pool.get("vmImage", "")))
            elif isinstance(pool, str):
                job_props["pool_type"] = pool

            display_name = job_dict.get("displayName")
            if display_name:
                job_props["displayName"] = str(display_name)

            variables = job_dict.get("variables")
            if isinstance(variables, dict):
                job_props["variables"] = _truncate(
                    ",".join(f"{k}={v}" for k, v in variables.items()),
                    200,
                )

            job_res = IaCResource(
                kind="CI_JOB",
                name=job_name,
                file=rel_path,
                properties=job_props,
            )
            graph.resources.append(job_res)
            graph.edges.append(IaCEdge(
                source_name=parent_name,
                target_name=job_name,
                relation="contains",
            ))

            # Steps within job
            steps = job_dict.get("steps", [])
            if isinstance(steps, list):
                self._parse_azure_steps(graph, rel_path, steps, job_name)

    def _parse_azure_steps(  # noqa: C901, PLR0912, PLR0915
        self,
        graph: IaCGraph,
        rel_path: str,
        steps: list[Any],
        parent_name: str,
    ) -> None:
        """Parse Azure Pipelines steps within a job."""
        seen_names: set[str] = set()
        for step_dict in steps:
            if not isinstance(step_dict, dict):
                continue

            step_props: dict[str, str] = {}

            # Determine step type and name
            if "template" in step_dict:
                template_ref = str(step_dict["template"])
                step_name = self._unique_name(
                    f"tmpl:{Path(template_ref.split('@', 1)[0]).stem}",
                    seen_names,
                )
                step_props["type"] = "template"
                step_props["template_ref"] = template_ref
                params = step_dict.get("parameters")
                if isinstance(params, dict):
                    for k, v in params.items():
                        step_props[f"param_{k}"] = _truncate(v)

            elif "script" in step_dict:
                display = step_dict.get("displayName", "")
                script_text = str(step_dict["script"])
                step_name = self._unique_name(
                    str(display) if display else _truncate(_first_line(script_text), 40),
                    seen_names,
                )
                step_props["type"] = "script"
                step_props["script"] = _truncate(script_text, 200)
                if display:
                    step_props["displayName"] = str(display)

            elif "bash" in step_dict:
                display = step_dict.get("displayName", "")
                step_name = self._unique_name(
                    str(display) if display else "bash",
                    seen_names,
                )
                step_props["type"] = "bash"
                step_props["script"] = _truncate(step_dict["bash"], 200)
                if display:
                    step_props["displayName"] = str(display)

            elif "powershell" in step_dict:
                display = step_dict.get("displayName", "")
                step_name = self._unique_name(
                    str(display) if display else "powershell",
                    seen_names,
                )
                step_props["type"] = "powershell"
                step_props["script"] = _truncate(step_dict["powershell"], 200)
                if display:
                    step_props["displayName"] = str(display)

            elif "task" in step_dict:
                task_name = str(step_dict["task"])
                display = step_dict.get("displayName", "")
                step_name = self._unique_name(
                    str(display) if display else task_name,
                    seen_names,
                )
                step_props["type"] = "task"
                step_props["task"] = task_name
                if display:
                    step_props["displayName"] = str(display)
                inputs = step_dict.get("inputs")
                if isinstance(inputs, dict):
                    step_props["inputs"] = _truncate(
                        ",".join(f"{k}={v}" for k, v in inputs.items()),
                        200,
                    )

            elif "download" in step_dict:
                dl_name = str(step_dict["download"])
                step_name = self._unique_name(
                    f"download:{dl_name}", seen_names,
                )
                step_props["type"] = "download"
                step_props["pipeline"] = dl_name

            elif "checkout" in step_dict:
                repo = str(step_dict["checkout"])
                step_name = self._unique_name(
                    f"checkout:{repo}", seen_names,
                )
                step_props["type"] = "checkout"
                step_props["repo"] = repo

            else:
                # Unknown step type — skip
                continue

            step_res = IaCResource(
                kind="CI_STEP",
                name=step_name,
                file=rel_path,
                properties=step_props,
            )
            graph.resources.append(step_res)
            graph.edges.append(IaCEdge(
                source_name=parent_name,
                target_name=step_name,
                relation="contains",
            ))

            # Template step → USES_TEMPLATE edge
            if step_props.get("type") == "template":
                graph.edges.append(IaCEdge(
                    source_name=parent_name,
                    target_name=step_name,
                    relation="uses_template",
                ))

    # endregion: --- Azure Pipelines
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- GitHub Actions
    # ------------------------------------------------------------------ #

    def _extract_github_actions(  # noqa: C901
        self,
        graph: IaCGraph,
        rel_path: str,
        doc: dict[str, Any],
        total_lines: int,
    ) -> None:
        """Extract GitHub Actions hierarchy: pipeline → jobs → steps."""
        pipeline_name = _safe_str(doc.get("name")) or Path(rel_path).stem
        pipeline_res = IaCResource(
            kind="CI_PIPELINE",
            name=pipeline_name,
            file=rel_path,
            span=Span(start_line=1, start_col=0, end_line=total_lines, end_col=0),
            properties={
                "platform": "github_actions",
                "trigger": _truncate(doc.get("on", "")),
            },
        )
        graph.resources.append(pipeline_res)

        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            return

        seen_job_names: set[str] = set()
        for job_key, job_dict in jobs.items():
            if not isinstance(job_dict, dict):
                continue

            job_name = self._unique_name(str(job_key), seen_job_names)
            job_props: dict[str, str] = {}

            runs_on = job_dict.get("runs-on")
            if runs_on:
                job_props["runs_on"] = _truncate(runs_on)

            # Reusable workflow reference
            uses = job_dict.get("uses")
            if uses:
                job_props["uses"] = str(uses)
                job_props["type"] = "reusable_workflow"

            strategy = job_dict.get("strategy")
            if isinstance(strategy, dict):
                matrix = strategy.get("matrix")
                if matrix:
                    job_props["matrix"] = _truncate(matrix, 200)

            needs = job_dict.get("needs")
            if needs:
                job_props["needs"] = (
                    ",".join(needs) if isinstance(needs, list) else str(needs)
                )

            job_res = IaCResource(
                kind="CI_JOB",
                name=job_name,
                file=rel_path,
                properties=job_props,
            )
            graph.resources.append(job_res)
            graph.edges.append(IaCEdge(
                source_name=pipeline_name,
                target_name=job_name,
                relation="contains",
            ))

            # Reusable workflow → USES_TEMPLATE edge
            if uses:
                graph.edges.append(IaCEdge(
                    source_name=pipeline_name,
                    target_name=job_name,
                    relation="uses_template",
                ))

            # Steps within job
            steps = job_dict.get("steps", [])
            if isinstance(steps, list):
                self._parse_github_steps(graph, rel_path, steps, job_name)

    def _parse_github_steps(
        self,
        graph: IaCGraph,
        rel_path: str,
        steps: list[Any],
        parent_name: str,
    ) -> None:
        """Parse GitHub Actions steps within a job."""
        seen_names: set[str] = set()
        for step_dict in steps:
            if not isinstance(step_dict, dict):
                continue

            step_props: dict[str, str] = {}
            step_display = step_dict.get("name", "")

            if "uses" in step_dict:
                action = str(step_dict["uses"])
                step_name = self._unique_name(
                    str(step_display) if step_display else action,
                    seen_names,
                )
                step_props["type"] = "uses"
                step_props["action"] = action
                with_params = step_dict.get("with")
                if isinstance(with_params, dict):
                    step_props["with"] = _truncate(
                        ",".join(f"{k}={v}" for k, v in with_params.items()),
                        200,
                    )

            elif "run" in step_dict:
                script = str(step_dict["run"])
                step_name = self._unique_name(
                    str(step_display) if step_display else _truncate(_first_line(script), 40),
                    seen_names,
                )
                step_props["type"] = "run"
                step_props["script"] = _truncate(script, 200)
                shell = step_dict.get("shell")
                if shell:
                    step_props["shell"] = str(shell)

            else:
                continue

            if step_display:
                step_props["displayName"] = str(step_display)

            step_res = IaCResource(
                kind="CI_STEP",
                name=step_name,
                file=rel_path,
                properties=step_props,
            )
            graph.resources.append(step_res)
            graph.edges.append(IaCEdge(
                source_name=parent_name,
                target_name=step_name,
                relation="contains",
            ))

            # uses action → USES_TEMPLATE edge
            if step_props.get("type") == "uses":
                graph.edges.append(IaCEdge(
                    source_name=parent_name,
                    target_name=step_name,
                    relation="uses_template",
                ))

    # endregion: --- GitHub Actions
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # region:    --- Shared Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _unique_name(base: str, seen: set[str]) -> str:
        """Generate a unique name by appending #N if already seen."""
        if not base:
            base = "unnamed"
        name = base
        counter = 2
        while name in seen:
            name = f"{base}#{counter}"
            counter += 1
        seen.add(name)
        return name

    # endregion: --- Shared Helpers
    # ------------------------------------------------------------------ #

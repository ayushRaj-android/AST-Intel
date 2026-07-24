"""Tests for CICDExtractor — Azure Pipelines and GitHub Actions."""

from __future__ import annotations

from pathlib import Path

import pytest

from ast_intel.extractors.iac.cicd import CICDExtractor
from ast_intel.models.iac_model import IaCContext, IaCGraph

# ---------------------------------------------------------------------------
# region:    --- Fixtures
# ---------------------------------------------------------------------------

AZURE_SIMPLE = """\
name: $(MAJOR).$(MINOR).$(BUILD)
trigger: none
parameters:
  - name: debug
    displayName: "Enable debug output"
    type: boolean
    default: false
variables:
  MAJOR: 0
  MINOR: 1
stages:
  - stage: build
    jobs:
      - job: linux_build
        pool:
          type: linux
        steps:
          - script: make lint
            displayName: Linting
          - script: make test
            displayName: Tests
          - task: DotNetCoreCLI@2
            displayName: 'Build Project'
            inputs:
              command: build
              projects: '*.csproj'
"""

AZURE_EXTENDS = """\
name: $(MAJOR).$(MINOR).$(BUILD)
trigger: none
parameters:
  - name: debug
    type: boolean
    default: false
variables:
  TAG: "pullrequest"
resources:
  repositories:
    - repository: templates
      type: git
      name: OneBranch.Pipelines/GovernedTemplates
      ref: refs/heads/main
extends:
  template: v2/OneBranch.NonOfficial.CrossPlat.yml@templates
  parameters:
    stages:
      - stage: build
        jobs:
          - job: azurelinux
            pool:
              type: linux
            steps:
              - template: .pipelines/templates/common/auth.yml@self
                parameters:
                  target: linux_build_container
              - script: make build
                displayName: Build Release
"""

AZURE_TEMPLATE_JOBS = """\
trigger: none
resources:
  repositories:
    - repository: templates
      type: git
      name: OneBranch.Pipelines/GovernedTemplates
extends:
  template: v2/OneBranch.Official.CrossPlat.yml@templates
  parameters:
    stages:
      - stage: docker
        jobs:
          - template: .pipelines/templates/common/generate_signed_image.yml@self
            parameters:
              jobName: build_file_reconstruction
              serviceName: file-reconstruction
          - template: .pipelines/templates/common/generate_signed_image.yml@self
            parameters:
              jobName: build_approval_engine
              serviceName: approval-engine
"""

AZURE_RELEASE = """\
trigger: none
parameters:
  - name: serviceGroupOverride
    type: string
    default: "Microsoft.Azure.Service.Dev"
resources:
  repositories:
    - repository: templates
      type: git
      name: OneBranch.Pipelines/GovernedTemplates
  pipelines:
    - pipeline: MyBuild
      source: MyBuild-Official
extends:
  template: v2/OneBranch.NonOfficial.CrossPlat.yml@templates
  parameters:
    stages:
      - stage: Test_Deployment
        displayName: "Dev Deployment using EV2"
        variables:
          ob_release_environment: Test
        jobs:
          - job: Release_Job
            displayName: "EV2 Deployment"
            pool:
              type: release
            steps:
              - download: MyBuild
              - task: Ev2RARollout@2
                displayName: "Ev2 Region Agnostic Dev"
                inputs:
                  ConnectedServiceName: "Ev2_to_Corp_subscription"
                  TaskAction: RegisterAndRollout
"""

AZURE_TEMPLATE_STEPS_FILE = """\
parameters:
  - name: target
    type: string
steps:
  - script: make restore
    displayName: "Restore dependencies"
  - script: make lint
    displayName: Linting
  - script: make test
    displayName: Tests
"""

GITHUB_SIMPLE = """\
name: CI
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install deps
        run: npm install
      - name: Run tests
        run: npm test
"""

GITHUB_MATRIX = """\
name: Test Matrix
on:
  push:
    branches: [main]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        node: [18, 20]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 18
      - run: npm test
"""

GITHUB_REUSABLE = """\
name: Deploy
on:
  workflow_dispatch:
jobs:
  call-build:
    uses: ./.github/workflows/build.yml
    with:
      environment: production
  deploy:
    needs: call-build
    runs-on: ubuntu-latest
    steps:
      - run: echo "deploying"
"""

NOT_CICD_YAML = """\
database:
  host: localhost
  port: 5432
  name: myapp
logging:
  level: info
"""

K8S_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-server
spec:
  replicas: 3
"""

MULTI_STAGE_AZURE = """\
trigger:
  branches:
    include: [main]
stages:
  - stage: build
    jobs:
      - job: compile
        steps:
          - script: make build
            displayName: Build
  - stage: test
    jobs:
      - job: unit_tests
        steps:
          - script: make test
            displayName: Test
  - stage: deploy
    jobs:
      - job: release
        steps:
          - script: make deploy
            displayName: Deploy
"""


@pytest.fixture
def extractor() -> CICDExtractor:
    return CICDExtractor()


def _ctx(rel: str = "pipeline.yml") -> IaCContext:
    return IaCContext(workspace_root="/repo", rel_path=rel)


def _extract(
    extractor: CICDExtractor,
    source: str,
    rel_path: str = "pipeline.yml",
    file_path: str | None = None,
) -> IaCGraph:
    fp = Path(file_path) if file_path else Path(f"/repo/{rel_path}")
    return extractor.extract(fp, source.encode(), _ctx(rel_path))


# endregion: --- Fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Detection tests
# ---------------------------------------------------------------------------

class TestCICDDetection:
    """Test can_handle heuristics for CI/CD files."""

    def test_detect_azure_by_pipelines_path(
        self, extractor: CICDExtractor,
    ) -> None:
        """File in .pipelines/ directory is detected."""
        path = Path("/repo/.pipelines/build.yml")
        assert extractor.can_handle(path, b"trigger: none\nstages:\n  - stage: x")

    def test_detect_azure_by_content(
        self, extractor: CICDExtractor,
    ) -> None:
        """Azure Pipelines with trigger + stages is detected."""
        path = Path("/repo/ci.yml")
        peek = b"trigger: none\nstages:\n  - stage: build"
        assert extractor.can_handle(path, peek)

    def test_detect_github_by_workflow_path(
        self, extractor: CICDExtractor,
    ) -> None:
        """File in .github/workflows/ is detected."""
        path = Path("/repo/.github/workflows/ci.yml")
        assert extractor.can_handle(path, b"name: CI\non: push\njobs:\n  build:")

    def test_detect_github_by_content(
        self, extractor: CICDExtractor,
    ) -> None:
        """GitHub Actions with on + jobs is detected."""
        path = Path("/repo/workflow.yml")
        peek = b"name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu"
        assert extractor.can_handle(path, peek)

    def test_reject_non_cicd_yaml(
        self, extractor: CICDExtractor,
    ) -> None:
        """Random YAML config file is rejected."""
        path = Path("/repo/config.yml")
        peek = NOT_CICD_YAML.encode()
        assert not extractor.can_handle(path, peek)

    def test_reject_kubernetes_yaml(
        self, extractor: CICDExtractor,
    ) -> None:
        """K8s manifest is rejected."""
        path = Path("/repo/.pipelines/deploy.yml")
        peek = K8S_DEPLOYMENT.encode()
        assert not extractor.can_handle(path, peek)

    def test_reject_non_yaml_extension(
        self, extractor: CICDExtractor,
    ) -> None:
        """Non-YAML files are rejected."""
        path = Path("/repo/.pipelines/README.md")
        assert not extractor.can_handle(path, b"trigger: none\nstages:")

    def test_detect_extends_content(
        self, extractor: CICDExtractor,
    ) -> None:
        """Azure Pipelines with extends + stages is detected."""
        path = Path("/repo/build.yml")
        peek = b"extends:\n  template: foo\n  parameters:\n    stages:"
        assert extractor.can_handle(path, peek)


# endregion: --- Detection tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Azure Pipelines extraction tests
# ---------------------------------------------------------------------------

class TestAzurePipelinesExtraction:
    """Test Azure Pipelines extraction."""

    def test_simple_pipeline_resources(
        self, extractor: CICDExtractor,
    ) -> None:
        """Simple Azure pipeline extracts full hierarchy."""
        graph = _extract(extractor, AZURE_SIMPLE)
        kinds = [r.kind for r in graph.resources]
        assert kinds.count("CI_PIPELINE") == 1
        assert kinds.count("CI_STAGE") == 1
        assert kinds.count("CI_JOB") == 1
        assert kinds.count("CI_STEP") == 3

    def test_pipeline_variables(
        self, extractor: CICDExtractor,
    ) -> None:
        """Variables are stored in pipeline properties."""
        graph = _extract(extractor, AZURE_SIMPLE)
        pipeline = next(r for r in graph.resources if r.kind == "CI_PIPELINE")
        assert "variables" in pipeline.properties
        assert "MAJOR" in pipeline.properties["variables"]

    def test_pipeline_parameters(
        self, extractor: CICDExtractor,
    ) -> None:
        """Parameters are stored in pipeline properties."""
        graph = _extract(extractor, AZURE_SIMPLE)
        pipeline = next(r for r in graph.resources if r.kind == "CI_PIPELINE")
        assert "parameters" in pipeline.properties
        assert "debug" in pipeline.properties["parameters"]

    def test_extends_pattern_stages(
        self, extractor: CICDExtractor,
    ) -> None:
        """Stages under extends.parameters.stages are found."""
        graph = _extract(extractor, AZURE_EXTENDS)
        stages = [r for r in graph.resources if r.kind == "CI_STAGE"]
        assert len(stages) == 1
        assert stages[0].name == "build"

    def test_extends_template_edge(
        self, extractor: CICDExtractor,
    ) -> None:
        """extends: template generates USES_TEMPLATE edge."""
        graph = _extract(extractor, AZURE_EXTENDS)
        template_edges = [
            e for e in graph.edges if e.relation == "uses_template"
        ]
        # At least one: extends template + template step
        assert len(template_edges) >= 1
        # Check the extends template resource exists
        ext_res = [
            r for r in graph.resources
            if r.properties.get("type") == "extends_template"
        ]
        assert len(ext_res) == 1

    def test_template_step_uses_template_edge(
        self, extractor: CICDExtractor,
    ) -> None:
        """Template step reference creates USES_TEMPLATE edge."""
        graph = _extract(extractor, AZURE_EXTENDS)
        template_steps = [
            r for r in graph.resources
            if r.kind == "CI_STEP" and r.properties.get("type") == "template"
        ]
        assert len(template_steps) == 1
        assert "auth" in template_steps[0].name

    def test_template_job_uses_template_edge(
        self, extractor: CICDExtractor,
    ) -> None:
        """Job-level template ref produces CI_JOB + USES_TEMPLATE edge."""
        graph = _extract(extractor, AZURE_TEMPLATE_JOBS)
        template_jobs = [
            r for r in graph.resources
            if r.kind == "CI_JOB"
            and r.properties.get("type") == "template"
        ]
        assert len(template_jobs) == 2
        tmpl_edges = [
            e for e in graph.edges if e.relation == "uses_template"
        ]
        # At least 2 job-level + 1 extends
        assert len(tmpl_edges) >= 3

    def test_task_step_properties(
        self, extractor: CICDExtractor,
    ) -> None:
        """Task step has type=task, task name, and inputs."""
        graph = _extract(extractor, AZURE_SIMPLE)
        task_steps = [
            r for r in graph.resources
            if r.kind == "CI_STEP" and r.properties.get("type") == "task"
        ]
        assert len(task_steps) == 1
        assert task_steps[0].properties["task"] == "DotNetCoreCLI@2"
        assert "inputs" in task_steps[0].properties

    def test_script_step_properties(
        self, extractor: CICDExtractor,
    ) -> None:
        """Script step has type=script, script content."""
        graph = _extract(extractor, AZURE_SIMPLE)
        script_steps = [
            r for r in graph.resources
            if r.kind == "CI_STEP" and r.properties.get("type") == "script"
        ]
        assert len(script_steps) == 2
        assert any("make lint" in s.properties.get("script", "") for s in script_steps)

    def test_release_pipeline_triggers_edge(
        self, extractor: CICDExtractor,
    ) -> None:
        """resources.pipelines generates TRIGGERS edge."""
        graph = _extract(extractor, AZURE_RELEASE)
        triggers = [e for e in graph.edges if e.relation == "triggers"]
        assert len(triggers) == 1
        assert triggers[0].source_name == "MyBuild-Official"

    def test_download_step(
        self, extractor: CICDExtractor,
    ) -> None:
        """Download step is extracted with pipeline reference."""
        graph = _extract(extractor, AZURE_RELEASE)
        dl_steps = [
            r for r in graph.resources
            if r.kind == "CI_STEP" and r.properties.get("type") == "download"
        ]
        assert len(dl_steps) == 1
        assert dl_steps[0].properties["pipeline"] == "MyBuild"

    def test_multi_stage_containment(
        self, extractor: CICDExtractor,
    ) -> None:
        """CONTAINS edges form pipeline→stage→job→step hierarchy."""
        graph = _extract(extractor, MULTI_STAGE_AZURE)
        contains_edges = [e for e in graph.edges if e.relation == "contains"]
        stages = [r for r in graph.resources if r.kind == "CI_STAGE"]
        jobs = [r for r in graph.resources if r.kind == "CI_JOB"]
        steps = [r for r in graph.resources if r.kind == "CI_STEP"]
        assert len(stages) == 3
        assert len(jobs) == 3
        assert len(steps) == 3
        # 3 pipeline→stage + 3 stage→job + 3 job→step = 9
        assert len(contains_edges) == 9

    def test_template_steps_only_file(
        self, extractor: CICDExtractor,
    ) -> None:
        """Template file with only steps: is handled."""
        graph = _extract(
            extractor,
            AZURE_TEMPLATE_STEPS_FILE,
            rel_path=".pipelines/templates/build_steps.yml",
            file_path="/repo/.pipelines/templates/build_steps.yml",
        )
        # Template files with just parameters+steps get pipeline + steps
        steps = [r for r in graph.resources if r.kind == "CI_STEP"]
        assert len(steps) == 3

    def test_stage_display_name(
        self, extractor: CICDExtractor,
    ) -> None:
        """Stage displayName is stored as property."""
        graph = _extract(extractor, AZURE_RELEASE)
        stages = [r for r in graph.resources if r.kind == "CI_STAGE"]
        assert len(stages) == 1
        assert "displayName" in stages[0].properties


# endregion: --- Azure Pipelines extraction tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- GitHub Actions extraction tests
# ---------------------------------------------------------------------------

class TestGitHubActionsExtraction:
    """Test GitHub Actions extraction."""

    def test_simple_workflow(
        self, extractor: CICDExtractor,
    ) -> None:
        """Simple GHA extracts pipeline + job + steps."""
        graph = _extract(
            extractor, GITHUB_SIMPLE,
            rel_path=".github/workflows/ci.yml",
            file_path="/repo/.github/workflows/ci.yml",
        )
        kinds = [r.kind for r in graph.resources]
        assert kinds.count("CI_PIPELINE") == 1
        assert kinds.count("CI_JOB") == 1
        assert kinds.count("CI_STEP") == 3

    def test_workflow_trigger_properties(
        self, extractor: CICDExtractor,
    ) -> None:
        """on: config stored in pipeline properties."""
        graph = _extract(
            extractor, GITHUB_SIMPLE,
            rel_path=".github/workflows/ci.yml",
            file_path="/repo/.github/workflows/ci.yml",
        )
        pipeline = next(r for r in graph.resources if r.kind == "CI_PIPELINE")
        assert pipeline.properties["platform"] == "github_actions"
        assert "trigger" in pipeline.properties

    def test_uses_action_step(
        self, extractor: CICDExtractor,
    ) -> None:
        """uses: action creates CI_STEP with action property."""
        graph = _extract(
            extractor, GITHUB_SIMPLE,
            rel_path=".github/workflows/ci.yml",
            file_path="/repo/.github/workflows/ci.yml",
        )
        uses_steps = [
            r for r in graph.resources
            if r.kind == "CI_STEP" and r.properties.get("type") == "uses"
        ]
        assert len(uses_steps) == 1
        assert uses_steps[0].properties["action"] == "actions/checkout@v4"

    def test_run_step_properties(
        self, extractor: CICDExtractor,
    ) -> None:
        """run: script creates CI_STEP with script property."""
        graph = _extract(
            extractor, GITHUB_SIMPLE,
            rel_path=".github/workflows/ci.yml",
            file_path="/repo/.github/workflows/ci.yml",
        )
        run_steps = [
            r for r in graph.resources
            if r.kind == "CI_STEP" and r.properties.get("type") == "run"
        ]
        assert len(run_steps) == 2
        scripts = [s.properties["script"] for s in run_steps]
        assert any("npm install" in s for s in scripts)

    def test_matrix_strategy(
        self, extractor: CICDExtractor,
    ) -> None:
        """Matrix strategy is recorded in job properties."""
        graph = _extract(
            extractor, GITHUB_MATRIX,
            rel_path=".github/workflows/matrix.yml",
            file_path="/repo/.github/workflows/matrix.yml",
        )
        jobs = [r for r in graph.resources if r.kind == "CI_JOB"]
        assert len(jobs) == 1
        assert "matrix" in jobs[0].properties

    def test_reusable_workflow(
        self, extractor: CICDExtractor,
    ) -> None:
        """Reusable workflow creates CI_JOB + USES_TEMPLATE edge."""
        graph = _extract(
            extractor, GITHUB_REUSABLE,
            rel_path=".github/workflows/deploy.yml",
            file_path="/repo/.github/workflows/deploy.yml",
        )
        reusable_jobs = [
            r for r in graph.resources
            if r.kind == "CI_JOB" and r.properties.get("type") == "reusable_workflow"
        ]
        assert len(reusable_jobs) == 1
        assert ".github/workflows/build.yml" in reusable_jobs[0].properties["uses"]
        tmpl_edges = [e for e in graph.edges if e.relation == "uses_template"]
        assert len(tmpl_edges) >= 1

    def test_job_needs_dependency(
        self, extractor: CICDExtractor,
    ) -> None:
        """Job needs field is stored as property."""
        graph = _extract(
            extractor, GITHUB_REUSABLE,
            rel_path=".github/workflows/deploy.yml",
            file_path="/repo/.github/workflows/deploy.yml",
        )
        deploy_job = next(
            r for r in graph.resources
            if r.kind == "CI_JOB" and r.name == "deploy"
        )
        assert "needs" in deploy_job.properties
        assert "call-build" in deploy_job.properties["needs"]

    def test_uses_step_template_edge(
        self, extractor: CICDExtractor,
    ) -> None:
        """GitHub uses: step generates USES_TEMPLATE edge."""
        graph = _extract(
            extractor, GITHUB_SIMPLE,
            rel_path=".github/workflows/ci.yml",
            file_path="/repo/.github/workflows/ci.yml",
        )
        tmpl_edges = [e for e in graph.edges if e.relation == "uses_template"]
        assert len(tmpl_edges) >= 1


# endregion: --- GitHub Actions extraction tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Edge building / naming tests
# ---------------------------------------------------------------------------

class TestCICDEdgeBuilding:
    """Test edge building and name disambiguation."""

    def test_step_name_disambiguation(
        self, extractor: CICDExtractor,
    ) -> None:
        """Steps with duplicate names get #N suffix."""
        source = """\
trigger: none
stages:
  - stage: build
    jobs:
      - job: test
        steps:
          - script: echo 1
            displayName: Same Name
          - script: echo 2
            displayName: Same Name
          - script: echo 3
            displayName: Same Name
"""
        graph = _extract(extractor, source)
        step_names = [r.name for r in graph.resources if r.kind == "CI_STEP"]
        assert len(step_names) == 3
        assert len(set(step_names)) == 3  # all unique
        assert "Same Name" in step_names
        assert "Same Name#2" in step_names

    def test_unique_node_ids_azure(
        self, extractor: CICDExtractor,
    ) -> None:
        """All resource names within a pipeline are unique."""
        graph = _extract(extractor, AZURE_EXTENDS)
        names = [r.name for r in graph.resources]
        assert len(names) == len(set(names))

    def test_unique_node_ids_github(
        self, extractor: CICDExtractor,
    ) -> None:
        """All GitHub Actions resource names are unique."""
        graph = _extract(
            extractor, GITHUB_SIMPLE,
            rel_path=".github/workflows/ci.yml",
            file_path="/repo/.github/workflows/ci.yml",
        )
        names = [r.name for r in graph.resources]
        assert len(names) == len(set(names))

    def test_contains_edges_form_tree(
        self, extractor: CICDExtractor,
    ) -> None:
        """CONTAINS edges form a proper tree hierarchy."""
        graph = _extract(extractor, AZURE_SIMPLE)
        contains = [e for e in graph.edges if e.relation == "contains"]
        resource_names = {r.name for r in graph.resources}
        for edge in contains:
            assert edge.source_name in resource_names
            assert edge.target_name in resource_names

    def test_error_on_invalid_yaml(
        self, extractor: CICDExtractor,
    ) -> None:
        """Invalid YAML produces errors in graph."""
        graph = _extract(extractor, "trigger: {\ninvalid yaml!!")
        assert len(graph.errors) > 0


# endregion: --- Edge building / naming tests
# ---------------------------------------------------------------------------

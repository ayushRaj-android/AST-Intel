"""Tests for the Ansible extractor."""

from __future__ import annotations

from pathlib import Path

from ast_intel.extractors.iac.ansible import AnsibleExtractor
from ast_intel.models.iac_model import IaCContext


# ---------------------------------------------------------------------------
# region:    --- Helpers
# ---------------------------------------------------------------------------


def _ctx(rel: str = "playbook.yml") -> IaCContext:
    return IaCContext(
        workspace_root=Path("/repo"),
        rel_path=rel,
    )


def _extractor() -> AnsibleExtractor:
    return AnsibleExtractor()


# endregion: --- Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Detection tests
# ---------------------------------------------------------------------------


class TestCanHandle:
    """Test can_handle detection heuristics."""

    def test_playbook_with_hosts_and_tasks(self) -> None:
        src = b"- hosts: all\n  tasks:\n    - name: test\n      debug: msg=hi"
        assert _extractor().can_handle(Path("site.yml"), src) is True

    def test_rejects_k8s(self) -> None:
        src = b"apiVersion: v1\nkind: Service\nmetadata:\n  name: web"
        assert _extractor().can_handle(Path("svc.yml"), src) is False

    def test_rejects_helm_chart(self) -> None:
        src = b"name: my-chart\nversion: 1.0.0"
        assert _extractor().can_handle(Path("Chart.yaml"), src) is False

    def test_import_playbook(self) -> None:
        src = b"- import_playbook: other.yml"
        assert _extractor().can_handle(Path("site.yml"), src) is True

    def test_include_playbook(self) -> None:
        src = b"- include_playbook: other.yml"
        assert _extractor().can_handle(Path("site.yml"), src) is True

    def test_tasks_in_playbooks_dir(self) -> None:
        src = b"tasks:\n  - name: do thing\n    command: ls"
        assert _extractor().can_handle(
            Path("playbooks/install.yml"), src,
        ) is True

    def test_handlers_in_playbooks_dir(self) -> None:
        src = b"handlers:\n  - name: restart\n    service: name=x"
        assert _extractor().can_handle(
            Path("playbooks/handlers.yml"), src,
        ) is True

    def test_role_tasks(self) -> None:
        src = b"tasks:\n  - name: install\n    apt: name=nginx"
        assert _extractor().can_handle(
            Path("roles/nginx/tasks/main.yml"), src,
        ) is True

    def test_rejects_plain_yaml(self) -> None:
        src = b"database:\n  host: localhost\n  port: 5432"
        assert _extractor().can_handle(Path("config.yml"), src) is False


# endregion: --- Detection tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Extraction tests
# ---------------------------------------------------------------------------


class TestExtractSimplePlaybook:
    """Test extracting a simple playbook."""

    def test_one_play_two_tasks(self) -> None:
        src = b"""\
- name: Setup servers
  hosts: all
  become: true
  tasks:
    - name: Install nginx
      apt:
        name: nginx
    - name: Start nginx
      service:
        name: nginx
        state: started
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        # 1 playbook + 2 tasks = 3 resources
        playbooks = [
            r for r in graph.resources if r.kind == "AnsiblePlaybook"
        ]
        tasks = [r for r in graph.resources if r.kind == "AnsibleTask"]
        assert len(playbooks) == 1
        assert len(tasks) == 2
        assert playbooks[0].name == "Setup servers"
        assert playbooks[0].properties["hosts"] == "all"
        assert playbooks[0].properties["become"] == "True"

        # 2 RUNS_TASK edges
        runs_task = [e for e in graph.edges if e.relation == "runs_task"]
        assert len(runs_task) == 2

    def test_task_module_detection(self) -> None:
        src = b"""\
- name: Test play
  hosts: all
  tasks:
    - name: Run command
      ansible.builtin.command: ls -la
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        tasks = [r for r in graph.resources if r.kind == "AnsibleTask"]
        assert len(tasks) == 1
        assert tasks[0].properties["module"] == "ansible.builtin.command"

    def test_task_when_condition(self) -> None:
        src = b"""\
- name: Conditional play
  hosts: all
  tasks:
    - name: Only when defined
      debug: msg=hi
      when: my_var is defined
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        tasks = [r for r in graph.resources if r.kind == "AnsibleTask"]
        assert len(tasks) == 1
        assert "when" in tasks[0].properties


class TestExtractHandlersNotify:
    """Test handler and notify extraction."""

    def test_notify_creates_edge(self) -> None:
        src = b"""\
- name: Web setup
  hosts: all
  tasks:
    - name: Install config
      template:
        src: nginx.conf
        dest: /etc/nginx/nginx.conf
      notify: restart nginx
  handlers:
    - name: restart nginx
      service:
        name: nginx
        state: restarted
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        notify_edges = [
            e for e in graph.edges if e.relation == "notifies_handler"
        ]
        assert len(notify_edges) == 1
        assert notify_edges[0].source_name == "Install config"
        assert notify_edges[0].target_name == "restart nginx"

        # Handler appears as a task with section=handlers
        handlers = [
            r for r in graph.resources
            if r.kind == "AnsibleTask"
            and r.properties.get("section") == "handlers"
        ]
        assert len(handlers) == 1


class TestExtractRoles:
    """Test role extraction."""

    def test_roles_list(self) -> None:
        src = b"""\
- name: Apply roles
  hosts: all
  roles:
    - common
    - nginx
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        roles = [r for r in graph.resources if r.kind == "AnsibleRole"]
        assert len(roles) == 2
        assert {r.name for r in roles} == {"common", "nginx"}

        uses_role = [e for e in graph.edges if e.relation == "uses_role"]
        assert len(uses_role) == 2

    def test_role_dict_form(self) -> None:
        src = b"""\
- name: Apply roles
  hosts: all
  roles:
    - role: common
      vars:
        key: value
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        roles = [r for r in graph.resources if r.kind == "AnsibleRole"]
        assert len(roles) == 1
        assert roles[0].name == "common"


class TestExtractImportPlaybook:
    """Test import_playbook handling."""

    def test_import_creates_edge(self) -> None:
        src = b"""\
- import_playbook: install.yml
- import_playbook: configure.yml
"""
        graph = _extractor().extract(
            Path("site.yml"), src, _ctx("site.yml"),
        )
        import_edges = [
            e for e in graph.edges if e.relation == "imports_playbook"
        ]
        assert len(import_edges) == 2
        targets = {e.target_name for e in import_edges}
        assert "install" in targets
        assert "configure" in targets


class TestExtractPrePostTasks:
    """Test pre_tasks, tasks, post_tasks sections."""

    def test_all_sections(self) -> None:
        src = b"""\
- name: Full play
  hosts: all
  pre_tasks:
    - name: Pre step
      debug: msg=pre
  tasks:
    - name: Main step
      debug: msg=main
  post_tasks:
    - name: Post step
      debug: msg=post
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        tasks = [r for r in graph.resources if r.kind == "AnsibleTask"]
        assert len(tasks) == 3
        sections = {t.properties["section"] for t in tasks}
        assert sections == {"pre_tasks", "tasks", "post_tasks"}


class TestExtractVarsKeysOnly:
    """Test that only var keys are stored, not values."""

    def test_vars_keys(self) -> None:
        src = b"""\
- name: Vars play
  hosts: all
  vars:
    secret_password: supersecret123
    database_host: db.example.com
  tasks:
    - name: Use vars
      debug: msg=hi
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        playbooks = [
            r for r in graph.resources if r.kind == "AnsiblePlaybook"
        ]
        assert len(playbooks) == 1
        vars_str = playbooks[0].properties.get("vars", "")
        assert "database_host" in vars_str
        assert "secret_password" in vars_str
        # Values must NOT be stored
        assert "supersecret123" not in vars_str
        assert "db.example.com" not in vars_str


class TestYamlParseError:
    """Test invalid YAML handling."""

    def test_parse_error(self) -> None:
        src = b"- hosts: all\n  tasks:\n    : invalid: yaml: {{{"
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        assert len(graph.errors) > 0


class TestMultiDocumentYaml:
    """Test multi-document YAML."""

    def test_two_documents(self) -> None:
        src = b"""\
---
- name: Play 1
  hosts: web
  tasks:
    - name: Task A
      debug: msg=a
---
- name: Play 2
  hosts: db
  tasks:
    - name: Task B
      debug: msg=b
"""
        graph = _extractor().extract(Path("pb.yml"), src, _ctx())
        playbooks = [
            r for r in graph.resources if r.kind == "AnsiblePlaybook"
        ]
        assert len(playbooks) == 2
        assert {p.name for p in playbooks} == {"Play 1", "Play 2"}


# endregion: --- Extraction tests
# ---------------------------------------------------------------------------

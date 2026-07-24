"""Tests for IaCDiscovery — IaC file discovery."""

from __future__ import annotations

from pathlib import Path

from ast_intel.core.iac_discovery import IaCDiscovery


class TestIaCDiscovery:
    """Tests for IaC file discovery."""

    def test_discovers_k8s_yaml(self, tmp_path: Path) -> None:
        """YAML file with apiVersion+kind is discovered."""
        f = tmp_path / "deploy.yaml"
        f.write_text("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n")
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 1
        assert files[0].rel_path == "deploy.yaml"

    def test_skips_plain_yaml(self, tmp_path: Path) -> None:
        """YAML without K8s markers is skipped."""
        f = tmp_path / "config.yaml"
        f.write_text("database:\n  host: localhost\n")
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 0

    def test_skips_default_excluded_dirs(self, tmp_path: Path) -> None:
        """Files in node_modules, .git, etc. are skipped."""
        d = tmp_path / "node_modules"
        d.mkdir()
        f = d / "deploy.yaml"
        f.write_text("apiVersion: v1\nkind: Service\nmetadata:\n  name: x\n")
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 0

    def test_discovers_dockerfile(self, tmp_path: Path) -> None:
        """Dockerfile is always discovered (by filename heuristic)."""
        f = tmp_path / "Dockerfile"
        f.write_text("FROM ubuntu:24.04\nRUN apt-get update\n")
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 1
        assert files[0].rel_path == "Dockerfile"

    def test_discovers_chart_yaml(self, tmp_path: Path) -> None:
        """Chart.yaml is always discovered."""
        f = tmp_path / "Chart.yaml"
        f.write_text("name: myapp\nversion: 1.0.0\n")
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 1

    def test_discovers_terraform_files(self, tmp_path: Path) -> None:
        """.tf files are always discovered."""
        f = tmp_path / "main.tf"
        f.write_text('resource "aws_instance" "web" {}\n')
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 1

    def test_respects_include_paths(self, tmp_path: Path) -> None:
        """Only files under include paths are discovered."""
        d1 = tmp_path / "deploy"
        d1.mkdir()
        d2 = tmp_path / "src"
        d2.mkdir()
        (d1 / "app.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n",
        )
        (d2 / "other.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: y\n",
        )
        discovery = IaCDiscovery(
            repo_root=tmp_path, include_paths=["deploy"],
        )
        files = discovery.discover()
        assert len(files) == 1
        assert "deploy" in files[0].rel_path

    def test_respects_exclude_paths(self, tmp_path: Path) -> None:
        """Files matching exclude patterns are excluded."""
        d = tmp_path / "generated"
        d.mkdir()
        (d / "app.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n",
        )
        discovery = IaCDiscovery(
            repo_root=tmp_path, exclude_paths=["generated/**"],
        )
        files = discovery.discover()
        assert len(files) == 0

    def test_skips_gitignored_files(self, tmp_path: Path) -> None:
        """Files matching .gitignore are excluded."""
        (tmp_path / ".gitignore").write_text("ignored/\n")
        d = tmp_path / "ignored"
        d.mkdir()
        (d / "deploy.yaml").write_text(
            "apiVersion: v1\nkind: Service\nmetadata:\n  name: x\n",
        )
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 0

    def test_skips_large_files(self, tmp_path: Path) -> None:
        """Files over 10 MB are skipped."""
        f = tmp_path / "huge.yaml"
        f.write_bytes(b"apiVersion: v1\nkind: ConfigMap\n" + b"x" * (11 * 1024 * 1024))
        discovery = IaCDiscovery(repo_root=tmp_path)
        files = discovery.discover()
        assert len(files) == 0

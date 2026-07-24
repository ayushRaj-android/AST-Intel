"""Tests for Feature 23 — Watch Mode (Auto-Rebuild)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

from ast_intel.core._watcher import (
    FileWatcher,
    WatchConfig,
    _Debouncer,
    _IgnoreFilter,
    _WatchEventHandler,
)

# ---------------------------------------------------------------------------
# region:    --- WatchConfig Tests
# ---------------------------------------------------------------------------


class TestWatchConfig:
    """Tests for WatchConfig defaults and construction."""

    def test_defaults(self, tmp_path: Path) -> None:
        config = WatchConfig(repo_root=tmp_path, output_dir=tmp_path / "out")
        assert config.debounce_ms == 500
        assert config.workers == 0
        assert config.no_methods is False
        assert config.similarity is False
        assert config.similarity_threshold == 0.4
        assert config.no_iac is False
        assert config.languages is None
        assert config.include is None
        assert config.exclude is None

    def test_custom_values(self, tmp_path: Path) -> None:
        config = WatchConfig(
            repo_root=tmp_path,
            output_dir=tmp_path / "out",
            debounce_ms=1000,
            workers=4,
            languages=["rust"],
            include=["src/"],
            exclude=["vendor/"],
        )
        assert config.debounce_ms == 1000
        assert config.workers == 4
        assert config.languages == ["rust"]


# endregion: --- WatchConfig Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- Debouncer Tests
# ---------------------------------------------------------------------------


class TestDebouncer:
    """Tests for the _Debouncer coalescing logic."""

    def test_fires_after_silence(self) -> None:
        """Single event fires callback after debounce delay."""
        result: list[set[str]] = []
        event = threading.Event()

        def callback(batch: set[str]) -> None:
            result.append(batch)
            event.set()

        debouncer = _Debouncer(delay_s=0.1, callback=callback)
        debouncer.push("a.py")
        event.wait(timeout=2.0)

        assert len(result) == 1
        assert result[0] == {"a.py"}

    def test_coalesces_rapid_events(self) -> None:
        """Multiple rapid pushes produce a single callback."""
        result: list[set[str]] = []
        event = threading.Event()

        def callback(batch: set[str]) -> None:
            result.append(batch)
            event.set()

        debouncer = _Debouncer(delay_s=0.2, callback=callback)
        for i in range(10):
            debouncer.push(f"file_{i}.py")
            time.sleep(0.02)  # 20ms between pushes

        event.wait(timeout=2.0)

        assert len(result) == 1
        assert len(result[0]) == 10

    def test_resets_timer_on_new_event(self) -> None:
        """Timer resets when a new event arrives before debounce expires."""
        result: list[set[str]] = []
        event = threading.Event()

        def callback(batch: set[str]) -> None:
            result.append(batch)
            event.set()

        debouncer = _Debouncer(delay_s=0.15, callback=callback)
        debouncer.push("first.py")
        time.sleep(0.1)  # 100ms — less than 150ms debounce
        debouncer.push("second.py")
        event.wait(timeout=2.0)

        # Should have both in a single batch
        assert len(result) == 1
        assert "first.py" in result[0]
        assert "second.py" in result[0]

    def test_cancel_prevents_firing(self) -> None:
        """Cancelled debouncer never calls callback."""
        result: list[set[str]] = []

        def callback(batch: set[str]) -> None:
            result.append(batch)

        debouncer = _Debouncer(delay_s=0.1, callback=callback)
        debouncer.push("x.py")
        debouncer.cancel()
        time.sleep(0.2)

        assert result == []

    def test_deduplicates_same_file(self) -> None:
        """Same file pushed multiple times appears once in batch."""
        result: list[set[str]] = []
        event = threading.Event()

        def callback(batch: set[str]) -> None:
            result.append(batch)
            event.set()

        debouncer = _Debouncer(delay_s=0.1, callback=callback)
        for _ in range(5):
            debouncer.push("same.py")

        event.wait(timeout=2.0)
        assert len(result) == 1
        assert result[0] == {"same.py"}


# endregion: --- Debouncer Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- IgnoreFilter Tests
# ---------------------------------------------------------------------------


class TestIgnoreFilter:
    """Tests for _IgnoreFilter path filtering."""

    def test_ignores_output_dir(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ast_output"
        output_dir.mkdir()
        filt = _IgnoreFilter(tmp_path, output_dir)
        assert filt.should_ignore(output_dir / "graph.json") is True

    def test_ignores_git_dir(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        filt = _IgnoreFilter(tmp_path, tmp_path / "out")
        assert filt.should_ignore(git_dir / "config") is True

    def test_ignores_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        filt = _IgnoreFilter(tmp_path, tmp_path / "out")
        assert filt.should_ignore(nm / "index.js") is True

    def test_ignores_pycache(self, tmp_path: Path) -> None:
        pc = tmp_path / "src" / "__pycache__"
        pc.mkdir(parents=True)
        filt = _IgnoreFilter(tmp_path, tmp_path / "out")
        assert filt.should_ignore(pc / "mod.cpython-313.pyc") is True

    def test_allows_normal_source(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "main.py"
        src.parent.mkdir(parents=True)
        src.touch()
        filt = _IgnoreFilter(tmp_path, tmp_path / "out")
        assert filt.should_ignore(src) is False

    def test_respects_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.log\nbuild/\n")
        filt = _IgnoreFilter(tmp_path, tmp_path / "out")
        assert filt.should_ignore(tmp_path / "error.log") is True
        assert filt.should_ignore(tmp_path / "build" / "main.py") is True
        assert filt.should_ignore(tmp_path / "src" / "main.py") is False

    def test_respects_ast_intel_ignore(self, tmp_path: Path) -> None:
        (tmp_path / ".ast-intel-ignore").write_text("vendor/\n")
        filt = _IgnoreFilter(tmp_path, tmp_path / "out")
        assert filt.should_ignore(tmp_path / "vendor" / "lib.py") is True
        assert filt.should_ignore(tmp_path / "src" / "lib.py") is False

    def test_outside_repo_ignored(self, tmp_path: Path) -> None:
        filt = _IgnoreFilter(tmp_path / "repo", tmp_path / "out")
        assert filt.should_ignore(tmp_path / "other" / "file.py") is True


# endregion: --- IgnoreFilter Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- EventHandler Tests
# ---------------------------------------------------------------------------


class TestWatchEventHandler:
    """Tests for _WatchEventHandler filtering logic."""

    def _make_handler(
        self, tmp_path: Path,
    ) -> tuple[_WatchEventHandler, _Debouncer, list[set[str]]]:
        results: list[set[str]] = []
        debouncer = _Debouncer(
            delay_s=0.05, callback=results.append,
        )
        filt = _IgnoreFilter(tmp_path, tmp_path / "ast_output")
        handler = _WatchEventHandler(
            repo_root=tmp_path,
            ignore_filter=filt,
            debouncer=debouncer,
            source_extensions=frozenset({".py", ".rs", ".ts"}),
        )
        return handler, debouncer, results

    def test_accepts_source_file(self, tmp_path: Path) -> None:
        handler, debouncer, results = self._make_handler(tmp_path)
        (tmp_path / "main.py").touch()

        class FakeEvent:
            src_path = str(tmp_path / "main.py")
            is_directory = False

        handler.on_modified(FakeEvent())
        time.sleep(0.15)
        debouncer.cancel()
        assert any("main.py" in f for batch in results for f in batch)

    def test_rejects_non_source_extension(self, tmp_path: Path) -> None:
        handler, debouncer, results = self._make_handler(tmp_path)
        (tmp_path / "readme.md").touch()

        class FakeEvent:
            src_path = str(tmp_path / "readme.md")
            is_directory = False

        handler.on_modified(FakeEvent())
        time.sleep(0.15)
        debouncer.cancel()
        assert results == []

    def test_rejects_directory_events(self, tmp_path: Path) -> None:
        handler, debouncer, results = self._make_handler(tmp_path)

        class FakeEvent:
            src_path = str(tmp_path / "src")
            is_directory = True

        handler.on_created(FakeEvent())
        time.sleep(0.15)
        debouncer.cancel()
        assert results == []

    def test_rejects_ignored_path(self, tmp_path: Path) -> None:
        handler, debouncer, results = self._make_handler(tmp_path)
        output_dir = tmp_path / "ast_output"
        output_dir.mkdir()
        (output_dir / "graph.py").touch()

        class FakeEvent:
            src_path = str(output_dir / "graph.py")
            is_directory = False

        handler.on_modified(FakeEvent())
        time.sleep(0.15)
        debouncer.cancel()
        assert results == []

    def test_handles_moved_event(self, tmp_path: Path) -> None:
        handler, debouncer, results = self._make_handler(tmp_path)
        (tmp_path / "old.py").touch()
        (tmp_path / "new.py").touch()

        class FakeMovedEvent:
            src_path = str(tmp_path / "old.py")
            dest_path = str(tmp_path / "new.py")
            is_directory = False

        handler.on_moved(FakeMovedEvent())
        time.sleep(0.15)
        debouncer.cancel()
        all_paths = {f for batch in results for f in batch}
        assert any("old.py" in f for f in all_paths)
        assert any("new.py" in f for f in all_paths)


# endregion: --- EventHandler Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- FileWatcher Tests
# ---------------------------------------------------------------------------


class TestFileWatcher:
    """Tests for FileWatcher lifecycle."""

    def test_stop_terminates_run(self, tmp_path: Path) -> None:
        """Calling stop() from another thread exits the run loop."""
        config = WatchConfig(
            repo_root=tmp_path, output_dir=tmp_path / "out",
        )
        watcher = FileWatcher(config)
        started = threading.Event()

        def run_watcher() -> None:
            started.set()
            watcher.run(quiet=True)

        t = threading.Thread(target=run_watcher, daemon=True)
        t.start()
        started.wait(timeout=2.0)
        time.sleep(0.2)  # Let observer start
        watcher.stop()
        t.join(timeout=5.0)
        assert not t.is_alive()

    def test_rebuild_lock_prevents_concurrent(self, tmp_path: Path) -> None:
        """Only one rebuild runs at a time."""
        config = WatchConfig(
            repo_root=tmp_path, output_dir=tmp_path / "out",
        )
        watcher = FileWatcher(config)

        # Acquire the lock manually
        watcher._rebuild_lock.acquire()
        try:
            # _on_rebuild should skip (non-blocking acquire fails)
            with patch.object(watcher, "_do_rebuild") as mock_rebuild:
                watcher._on_rebuild({"test.py"}, quiet=True)
                mock_rebuild.assert_not_called()
        finally:
            watcher._rebuild_lock.release()

    def test_missing_watchdog_error(self, tmp_path: Path) -> None:
        """Clear error when watchdog is not installed."""
        config = WatchConfig(
            repo_root=tmp_path, output_dir=tmp_path / "out",
        )
        watcher = FileWatcher(config)

        with patch.dict(
            "sys.modules",
            {"watchdog": None, "watchdog.events": None, "watchdog.observers": None},
        ):
            import pytest

            with pytest.raises(SystemExit, match="watchdog"):
                watcher.run(quiet=True)


# endregion: --- FileWatcher Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# region:    --- CLI Tests
# ---------------------------------------------------------------------------


class TestWatchCLI:
    """Tests for the watch CLI command."""

    def test_watch_help(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["watch", "--help"])
        assert result.exit_code == 0
        assert "Watch a repository" in result.output
        assert "--debounce" in result.output

    def test_watch_invalid_path(self) -> None:
        from typer.testing import CliRunner

        from ast_intel.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["watch", "/nonexistent/path"])
        assert result.exit_code != 0


# endregion: --- CLI Tests
# ---------------------------------------------------------------------------

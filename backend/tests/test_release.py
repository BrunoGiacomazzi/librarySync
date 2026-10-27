import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib


@pytest.mark.parametrize(
    ("bump", "expected"),
    [("patch", "0.22.1"), ("minor", "0.23.0"), ("major", "1.0.0")],
)
def test_release_updates_versions_and_publishes_matching_tag(tmp_path, monkeypatch, bump, expected):
    spec = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parents[2] / "scripts/release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)

    origin = tmp_path / "origin.git"
    worktree = tmp_path / "worktree"
    backend = worktree / "backend"
    backend.mkdir(parents=True)
    (worktree / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["backend"]\n')
    version_file = backend / "pyproject.toml"
    version_file.write_text('[project]\nname = "librarysync"\nversion = "0.22.0"\nrequires-python = ">=3.13"\n')
    git_executable = shutil.which("git")

    def git(*args):
        return subprocess.run(  # noqa: S603
            [git_executable, *args], cwd=worktree, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--bare", str(origin))
    git("init", "-b", "main")
    git("config", "user.name", "Release test")
    git("config", "user.email", "release@example.com")
    git("add", ".")
    git("commit", "-m", "Initial version")
    git("remote", "add", "origin", str(origin))
    git("push", "-u", "origin", "main")

    monkeypatch.chdir(worktree)
    monkeypatch.setattr(release, "VERSION_FILES", (version_file,))
    monkeypatch.setattr(release, "UV_LOCK", worktree / "uv.lock")
    monkeypatch.setattr(release, "ROOT", worktree)
    monkeypatch.setattr(release, "run_checks", lambda: None)
    monkeypatch.setattr(release.shutil, "which", lambda name: name)
    monkeypatch.setattr(sys, "argv", ["release.py", f"--{bump}", "--skip-ci-check"])
    github_calls = []
    run = release.run

    def run_without_github(cmd):
        if cmd[0] == "gh":
            github_calls.append(cmd)
        else:
            run(cmd)

    monkeypatch.setattr(release, "run", run_without_github)
    assert release.main() == 0

    project = tomllib.loads(version_file.read_text())
    lock = tomllib.loads((worktree / "uv.lock").read_text())
    assert project["project"]["version"] == expected
    assert next(package["version"] for package in lock["package"] if package["name"] == "librarysync") == expected
    assert git("status", "--porcelain") == ""
    tag = f"v{expected}"
    assert git("rev-parse", "HEAD") == git("rev-parse", f"{tag}^{{commit}}")
    assert git("--git-dir", str(origin), "rev-parse", "main") == git("rev-parse", "HEAD")
    assert git("--git-dir", str(origin), "rev-parse", f"{tag}^{{commit}}") == git("rev-parse", "HEAD")
    assert github_calls == [["gh", "release", "create", tag, "--title", tag, "--generate-notes"]]

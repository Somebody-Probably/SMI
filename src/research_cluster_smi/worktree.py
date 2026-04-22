"""Git worktree isolation for SMI workers."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised when git worktree setup or preservation fails."""


def run_git(repo_root: Path, args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd or repo_root),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise WorktreeError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
    return completed


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or "item"


def find_repo_root(start: str | Path = ".") -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise WorktreeError("Not inside a git repository.")
    return Path(completed.stdout.strip()).resolve()


@dataclass(frozen=True)
class PreservedChanges:
    slot_id: str
    task_id: str
    branch: str
    commit: str | None
    worktree_path: str


class WorktreeManager:
    """Create per-slot git worktrees and preserve worker edits as commits."""

    def __init__(self, repo_root: str | Path, worktree_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root).resolve()
        if worktree_root is None:
            self.worktree_root = self.repo_root / ".smi-worktrees"
        else:
            root = Path(worktree_root)
            self.worktree_root = root if root.is_absolute() else self.repo_root / root
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        run_git(self.repo_root, ["rev-parse", "--is-inside-work-tree"])

    def branch_name(self, run_id: str, slot_id: str) -> str:
        return f"smi/{slug(run_id)}/{slug(slot_id)}"

    def worktree_path(self, run_id: str, slot_id: str) -> Path:
        return self.worktree_root / slug(run_id) / slug(slot_id)

    def ensure_worktree(self, run_id: str, slot_id: str) -> Path:
        path = self.worktree_path(run_id, slot_id)
        branch = self.branch_name(run_id, slot_id)
        if path.exists():
            run_git(self.repo_root, ["rev-parse", "--is-inside-work-tree"], cwd=path)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        run_git(self.repo_root, ["worktree", "add", "-B", branch, str(path), "HEAD"])
        return path

    def has_changes(self, path: Path) -> bool:
        completed = run_git(self.repo_root, ["status", "--porcelain"], cwd=path)
        return bool(completed.stdout.strip())

    def preserve_changes(self, run_id: str, slot_id: str, task_id: str) -> PreservedChanges:
        path = self.ensure_worktree(run_id, slot_id)
        branch = self.branch_name(run_id, slot_id)
        if not self.has_changes(path):
            return PreservedChanges(slot_id=slot_id, task_id=task_id, branch=branch, commit=None, worktree_path=str(path))
        run_git(self.repo_root, ["add", "-A"], cwd=path)
        run_git(self.repo_root, ["commit", "-m", f"SMI task {task_id} from {slot_id}"], cwd=path)
        commit = run_git(self.repo_root, ["rev-parse", "HEAD"], cwd=path).stdout.strip()
        return PreservedChanges(slot_id=slot_id, task_id=task_id, branch=branch, commit=commit, worktree_path=str(path))

    def remove_worktree(self, run_id: str, slot_id: str, *, force: bool = False) -> None:
        path = self.worktree_path(run_id, slot_id)
        if path.exists():
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(str(path))
            run_git(self.repo_root, args)


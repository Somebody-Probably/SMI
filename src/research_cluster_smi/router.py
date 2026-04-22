"""Router-mediated review and merge of SMI worktree branches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .worktree import WorktreeError, run_git, slug


@dataclass(frozen=True)
class PendingBranch:
    branch: str
    head: str
    subject: str
    changed_files: list[str]


class Router:
    """Review SMI branches and explicitly merge them into the active branch."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        run_git(self.repo_root, ["rev-parse", "--is-inside-work-tree"])

    def pending_branches(self, run_id: str | None = None) -> list[PendingBranch]:
        prefix = f"refs/heads/smi/{slug(run_id)}/" if run_id else "refs/heads/smi/"
        completed = run_git(
            self.repo_root,
            ["for-each-ref", "--format=%(refname:short)|%(objectname)|%(subject)", prefix],
            check=False,
        )
        if completed.returncode != 0:
            return []
        branches: list[PendingBranch] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            branch, head, subject = line.split("|", 2)
            branches.append(
                PendingBranch(
                    branch=branch,
                    head=head,
                    subject=subject,
                    changed_files=self.changed_files(branch),
                )
            )
        return branches

    def changed_files(self, branch: str, base: str = "HEAD") -> list[str]:
        completed = run_git(self.repo_root, ["diff", "--name-only", f"{base}...{branch}"], check=False)
        if completed.returncode != 0:
            completed = run_git(self.repo_root, ["diff", "--name-only", branch], check=False)
        return [line for line in completed.stdout.splitlines() if line.strip()]

    def merge_plan(self, branch: str, base: str = "HEAD") -> dict:
        commits = run_git(self.repo_root, ["rev-list", "--reverse", f"{base}..{branch}"], check=False)
        commit_ids = [line for line in commits.stdout.splitlines() if line.strip()] if commits.returncode == 0 else []
        return {
            "branch": branch,
            "base": base,
            "commits": commit_ids,
            "changed_files": self.changed_files(branch, base),
        }

    def merge_branch(self, branch: str, *, base: str = "HEAD", dry_run: bool = False) -> dict:
        plan = self.merge_plan(branch, base)
        if dry_run:
            return {**plan, "dry_run": True, "merged": False}
        status = run_git(self.repo_root, ["status", "--porcelain"]).stdout.strip()
        if status:
            raise WorktreeError("Router merge requires a clean git worktree.")
        if not plan["commits"]:
            return {**plan, "merged": False, "message": "No commits to merge."}
        run_git(self.repo_root, ["cherry-pick", *plan["commits"]])
        head = run_git(self.repo_root, ["rev-parse", "HEAD"]).stdout.strip()
        return {**plan, "merged": True, "head": head}

    @staticmethod
    def format_pending(branches: list[PendingBranch], *, json_output: bool = False) -> str:
        if json_output:
            return json.dumps([branch.__dict__ for branch in branches], indent=2)
        if not branches:
            return "No pending SMI branches."
        lines = []
        for branch in branches:
            files = ", ".join(branch.changed_files) if branch.changed_files else "no file diff from current HEAD"
            lines.append(f"{branch.branch} {branch.head[:10]} {branch.subject}\n  files: {files}")
        return "\n".join(lines)


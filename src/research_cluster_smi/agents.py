"""Agent command presets for SMI workers."""

from __future__ import annotations

import os
import shlex


SUPPORTED_AGENT_PRESETS = ("custom", "claude", "codex")


def build_codex_command(
    *,
    model: str | None = None,
    sandbox: str | None = None,
    profile: str | None = None,
    extra_args: str | None = None,
) -> str:
    """Build a Codex CLI command that reads the task prompt from stdin."""
    args = ["codex", "exec"]

    codex_profile = profile if profile is not None else os.environ.get("SMI_CODEX_PROFILE")
    if codex_profile:
        args.extend(["--profile", codex_profile])

    codex_model = model if model is not None else os.environ.get("SMI_CODEX_MODEL")
    if codex_model:
        args.extend(["--model", codex_model])

    codex_sandbox = sandbox if sandbox is not None else os.environ.get("SMI_CODEX_SANDBOX", "workspace-write")
    if codex_sandbox:
        args.extend(["--sandbox", codex_sandbox])

    codex_extra_args = extra_args if extra_args is not None else os.environ.get("SMI_CODEX_EXTRA_ARGS", "")
    if codex_extra_args:
        args.extend(shlex.split(codex_extra_args))

    args.append("-")
    return shlex.join(args)


def resolve_agent_command(
    *,
    agent_command: str | None = None,
    agent_preset: str | None = None,
    codex_model: str | None = None,
    codex_sandbox: str | None = None,
    codex_profile: str | None = None,
    codex_extra_args: str | None = None,
) -> str:
    """Return the shell-style command string a worker should launch."""
    if agent_command:
        return agent_command

    preset = (agent_preset or os.environ.get("SMI_AGENT_PRESET") or "custom").lower()
    if preset == "custom":
        return os.environ.get("SMI_AGENT_COMMAND", "claude --print")
    if preset == "claude":
        return os.environ.get("SMI_CLAUDE_COMMAND") or os.environ.get("SMI_AGENT_COMMAND", "claude --print")
    if preset == "codex":
        return build_codex_command(
            model=codex_model,
            sandbox=codex_sandbox,
            profile=codex_profile,
            extra_args=codex_extra_args,
        )

    supported = ", ".join(SUPPORTED_AGENT_PRESETS)
    raise ValueError(f"Unsupported agent preset: {preset}. Supported presets: {supported}")

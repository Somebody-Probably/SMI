from research_cluster_smi.agents import build_codex_command, resolve_agent_command


def test_resolve_agent_command_prefers_explicit_command() -> None:
    assert resolve_agent_command(agent_command="python fake_agent.py", agent_preset="codex") == "python fake_agent.py"


def test_codex_agent_command_reads_prompt_from_stdin() -> None:
    command = build_codex_command(model="gpt-5.4", sandbox="workspace-write", extra_args="--ephemeral")
    assert command == "codex exec --model gpt-5.4 --sandbox workspace-write --ephemeral -"


def test_resolve_codex_preset() -> None:
    command = resolve_agent_command(agent_preset="codex", codex_model="gpt-5.4-mini", codex_sandbox="read-only")
    assert command == "codex exec --model gpt-5.4-mini --sandbox read-only -"

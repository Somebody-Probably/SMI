import json
import sys
import time
from pathlib import Path

from research_cluster_smi.account_gate import AccountGate, account_gate_path
from research_cluster_smi.orders import OrderWatcher
from research_cluster_smi.smi_core import runtime_for
from research_cluster_smi.worker import WorkerManager


def test_smi_dry_run_smoke(tmp_path: Path) -> None:
    run_id = "smoke"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "hello.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Say hello.", encoding="utf-8")
        runtime.seed_task(
            run_id,
            "fast_local",
            task_id="hello",
            prompt_path=str(prompt),
            write_set=["notes/hello.md"],
        )
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        summary = worker.tick()
        assert summary == {"started": 1, "completed": 1}
        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 1
        result_path = runtime.run_dir / "results" / "hello" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["type"] == "dry_run"
    finally:
        runtime.close()


def test_smi_releases_blocked_dependencies(tmp_path: Path) -> None:
    run_id = "deps"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"remote_transfer": {"max_slots": 1}})
        prompt_dir = runtime.run_dir / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        stage_prompt = prompt_dir / "stage.md"
        submit_prompt = prompt_dir / "submit.md"
        stage_prompt.write_text("Stage files.", encoding="utf-8")
        submit_prompt.write_text("Submit job.", encoding="utf-8")
        runtime.seed_task(
            run_id,
            "remote_transfer",
            task_id="stage",
            prompt_path=str(stage_prompt),
            write_set=["remote/input"],
        )
        runtime.seed_task(
            run_id,
            "remote_transfer",
            task_id="submit",
            dependencies=["stage"],
            prompt_path=str(submit_prompt),
            write_set=["remote/job"],
        )

        status = runtime.status_summary(run_id)
        assert status["tasks"]["ready"] == 1
        assert status["tasks"]["blocked"] == 1

        worker = WorkerManager(runtime, run_id, "remote_transfer", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}

        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 1
        assert status["tasks"]["ready"] == 1

        assert worker.tick() == {"started": 1, "completed": 1}
        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 2
    finally:
        runtime.close()


def test_order_watcher_accepts_utf8_bom_seed_order(tmp_path: Path) -> None:
    run_id = "orders-bom"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        orders_dir = runtime.run_dir / "orders"
        orders_dir.mkdir(parents=True, exist_ok=True)
        order = {
            "order_type": "seed",
            "payload": {
                "tasks": [
                    {
                        "id": "hello-bom",
                        "lane": "fast_local",
                        "priority": 150,
                        "write_set": ["notes/hello-bom.md"],
                        "prompt": "Say hello from a BOM-authored order.",
                    }
                ]
            },
        }
        (orders_dir / "seed-bom.json").write_text(
            "\ufeff" + json.dumps(order),
            encoding="utf-8",
        )

        results = OrderWatcher(runtime, run_id, orders_dir).poll()

        assert len(results) == 1
        assert results[0].success is True
        assert runtime.status_summary(run_id)["tasks"]["ready"] == 1
        assert (runtime.run_dir / "prompts" / "hello-bom.md").exists()
    finally:
        runtime.close()


def test_smi_worker_can_launch_agent_command(tmp_path: Path) -> None:
    run_id = "agent-command"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "hello.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Say hello.", encoding="utf-8")
        agent = tmp_path / "agent.py"
        agent.write_text(
            "import sys\n"
            "prompt = sys.stdin.read()\n"
            "print('received:' + prompt)\n",
            encoding="utf-8",
        )
        runtime.seed_task(run_id, "fast_local", task_id="hello", prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "fast_local",
            slots=1,
            agent_command=f"{sys.executable} {agent}",
        )
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}

        result_path = runtime.run_dir / "results" / "hello" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["returncode"] == 0
        assert result["stdout"] == "received:Say hello.\n"
    finally:
        runtime.close()


def test_smi_worker_respects_busy_account_gate(tmp_path: Path) -> None:
    run_id = "project-a"
    runtime = runtime_for(tmp_path, run_id)
    gate = AccountGate("drac-fouquet", account_gate_path(tmp_path, "drac-fouquet"))
    try:
        gate.configure_resources({"remote_transfer": {"max_slots": 1}})
        held = gate.acquire("remote_transfer", holder="project-b")
        assert held is not None

        runtime.initialize_run(run_id, lanes={"remote_transfer": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "stage.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Stage files.", encoding="utf-8")
        runtime.seed_task(run_id, "remote_transfer", task_id="stage", prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "remote_transfer",
            slots=1,
            dry_run=True,
            account_gate=gate,
            account_gated_lanes={"remote_transfer"},
        )
        worker.register_slots()
        assert worker.tick() == {"started": 0, "completed": 0}
        assert runtime.status_summary(run_id)["tasks"]["ready"] == 1

        assert gate.release(held.permit_id)
        assert worker.tick() == {"started": 1, "completed": 1}
        result_path = runtime.run_dir / "results" / "stage" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["account_permit"]["resource"] == "remote_transfer"
    finally:
        gate.close()
        runtime.close()


def test_smi_parallel_worker_supervises_multiple_agents(tmp_path: Path) -> None:
    run_id = "parallel-agent-command"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 2}})
        prompt_dir = runtime.run_dir / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        agent = tmp_path / "agent.py"
        agent.write_text(
            "import os, sys, time\n"
            "prompt = sys.stdin.read()\n"
            "time.sleep(0.2)\n"
            "print(os.environ['SMI_TASK_ID'] + ':' + prompt)\n",
            encoding="utf-8",
        )
        for task_id in ("a", "b"):
            prompt = prompt_dir / f"{task_id}.md"
            prompt.write_text(task_id, encoding="utf-8")
            runtime.seed_task(run_id, "fast_local", task_id=task_id, prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "fast_local",
            slots=2,
            agent_command=f"{sys.executable} {agent}",
            parallel=True,
        )
        worker.register_slots()
        first_tick = worker.tick()
        assert first_tick == {"started": 2, "completed": 0}

        deadline = time.monotonic() + 5
        completed = 0
        while time.monotonic() < deadline and completed < 2:
            time.sleep(0.05)
            completed += worker.tick()["completed"]

        assert completed == 2
        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 2
    finally:
        runtime.close()

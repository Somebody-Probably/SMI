import json
from pathlib import Path

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


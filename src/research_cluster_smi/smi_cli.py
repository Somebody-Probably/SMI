"""Command line interface for the generic SMI runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .orders import OrderWatcher
from .smi_core import DEFAULT_LANES, runtime_for, run_dir_for
from .worker import WorkerManager


DEFAULT_RUN_ROOT = Path(os.environ.get("SMI_RUN_ROOT", "runs"))


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def materialize_prompt(run_dir: Path, task: dict[str, Any]) -> str | None:
    prompt_path = task.get("prompt_path")
    if task.get("prompt") and not prompt_path:
        prompts_dir = run_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        task_id = task.get("id") or task.get("task_id")
        prompt_file = prompts_dir / f"{task_id}.md"
        prompt_file.write_text(str(task["prompt"]), encoding="utf-8")
        return str(prompt_file)
    return prompt_path


def seed_from_spec(runtime, run_id: str, spec_path: str) -> int:
    spec = load_json(spec_path)
    tasks = spec.get("tasks", [])
    count = 0
    for index, task in enumerate(tasks):
        task_id = task.get("id") or task.get("task_id") or f"task-{index:03d}"
        prompt_path = materialize_prompt(runtime.run_dir, {**task, "id": task_id})
        metadata = dict(task.get("metadata") or {})
        for key, value in task.items():
            if key not in {
                "id",
                "task_id",
                "lane",
                "priority",
                "dependencies",
                "write_set",
                "prompt",
                "prompt_path",
                "metadata",
            }:
                metadata.setdefault(key, value)
        runtime.seed_task(
            run_id,
            task.get("lane", "fast_local"),
            task_id=task_id,
            priority=int(task.get("priority", 100)),
            dependencies=task.get("dependencies") or [],
            write_set=task.get("write_set") or [],
            prompt_path=prompt_path,
            metadata=metadata,
        )
        count += 1
    return count


def cmd_init(args: argparse.Namespace) -> int:
    lanes = DEFAULT_LANES
    if args.lanes_json:
        lanes = {**DEFAULT_LANES, **load_json(args.lanes_json)}
    rd = run_dir_for(args.run_root, args.run_id)
    (rd / "orders" / "processed").mkdir(parents=True, exist_ok=True)
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        runtime.initialize_run(args.run_id, lanes=lanes)
        for lane, config in lanes.items():
            slots = int(config.get("max_slots", 1))
            for index in range(slots):
                runtime.register_slot(args.run_id, lane, f"{lane}-{index:02d}")
        seeded = seed_from_spec(runtime, args.run_id, args.spec) if args.spec else 0
    finally:
        runtime.close()
    print(f"Initialized run {args.run_id}")
    print(f"Run directory: {rd}")
    print(f"Seeded tasks: {seeded}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        summary = runtime.status_summary(args.run_id)
    finally:
        runtime.close()
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"SMI run: {summary['run_id']}")
    print(f"Status : {summary['status']}")
    print(f"Run dir: {summary['run_dir']}")
    print("Tasks:")
    if summary["tasks"]:
        for status, count in sorted(summary["tasks"].items()):
            print(f"  {status:<14} {count}")
    else:
        print("  none")
    print("Lanes:")
    for lane in summary["lanes"]:
        print(
            f"  {lane['lane']:<16} max={lane['max_slots']} "
            f"admission={lane['admission_state']} backpressure={lane['backpressure']}"
        )
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        count = seed_from_spec(runtime, args.run_id, args.spec)
    finally:
        runtime.close()
    print(f"Seeded {count} task(s) into run {args.run_id}.")
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        watcher = OrderWatcher(runtime, args.run_id, runtime.run_dir / "orders")
        results = watcher.poll()
    finally:
        runtime.close()
    if not results:
        print("No orders pending.")
    for result in results:
        status = "OK" if result.success else "FAIL"
        print(f"{status} {result.filename}: {result.order_type}: {result.message}")
    return 0 if all(result.success for result in results) else 1


def cmd_worker(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        manager = WorkerManager(
            runtime,
            args.run_id,
            args.lane,
            slots=args.slots,
            dry_run=args.dry_run,
            agent_command=args.agent_command,
            tick_interval=args.tick_interval,
        )
        manager.run(once=args.once, max_ticks=args.max_ticks)
    finally:
        runtime.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        lanes = [lane.strip() for lane in args.lanes.split(",") if lane.strip()]
        managers = [
            WorkerManager(
                runtime,
                args.run_id,
                lane,
                slots=args.slots,
                dry_run=args.dry_run,
                agent_command=args.agent_command,
                tick_interval=args.tick_interval,
            )
            for lane in lanes
        ]
        for manager in managers:
            manager.register_slots()
        watcher = OrderWatcher(runtime, args.run_id, runtime.run_dir / "orders")
        tick = 0
        while True:
            tick += 1
            order_results = watcher.poll()
            for result in order_results:
                status = "OK" if result.success else "FAIL"
                print(f"order {status}: {result.filename}: {result.message}")
            started = 0
            completed = 0
            for manager in managers:
                summary = manager.tick()
                started += summary["started"]
                completed += summary["completed"]
            print(f"tick={tick} started={started} completed={completed}")
            if args.once or (args.max_ticks is not None and tick >= args.max_ticks):
                break
            time.sleep(args.tick_interval)
    finally:
        runtime.close()
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    return _set_status(args, "paused")


def cmd_resume(args: argparse.Namespace) -> int:
    return _set_status(args, "active")


def cmd_drain(args: argparse.Namespace) -> int:
    return _set_status(args, "draining")


def _set_status(args: argparse.Namespace, status: str) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        runtime.set_run_status(args.run_id, status)
    finally:
        runtime.close()
    print(f"Run {args.run_id}: {status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-smi")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT), help="Directory holding SMI run state.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Initialize a run.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--spec", help="Task spec JSON to seed.")
    p.add_argument("--lanes-json", help="Lane config JSON.")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="Show run status.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("seed", help="Seed tasks from a JSON spec.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--spec", required=True)
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("orders", help="Process pending hot-folder orders once.")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("worker", help="Run one lane worker manager.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--lane", default="fast_local")
    p.add_argument("--slots", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--agent-command")
    p.add_argument("--tick-interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--max-ticks", type=int)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("run", help="Run order watcher and workers in one loop.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--lanes", default="fast_local")
    p.add_argument("--slots", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--agent-command")
    p.add_argument("--tick-interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--max-ticks", type=int)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("pause")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("resume")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("drain")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_drain)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

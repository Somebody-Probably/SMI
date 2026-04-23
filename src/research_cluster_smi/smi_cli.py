"""Command line interface for the generic SMI runtime."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .account_cluster import AccountClusterRunner
from .account_gate import AccountGate, account_gate_path, load_resource_config
from .agents import SUPPORTED_AGENT_PRESETS
from .orders import OrderWatcher
from .router import Router
from .smi_core import DEFAULT_LANES, runtime_for, run_dir_for, utc_now
from .verification import reconciliation_preview, verify_completed_attempts
from .worker import WorkerManager


DEFAULT_RUN_ROOT = Path(os.environ.get("SMI_RUN_ROOT", "runs"))


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
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
    if summary.get("leases"):
        print("Leases:")
        for status, count in sorted(summary["leases"].items()):
            print(f"  {status:<14} {count}")
    if summary.get("active_leases"):
        print("Active leases:")
        for lease in summary["active_leases"]:
            print(
                f"  {lease['lease_id']} task={lease['task_id']} slot={lease['slot_id']} "
                f"age={lease['age_seconds']}s heartbeat_age={lease['heartbeat_age_seconds']}s "
                f"expires_in={lease['expires_in_seconds']}s expires={lease['expires_at']}"
            )
    if summary.get("verification_pending") or summary.get("verification_current") or summary.get("verifications"):
        print("Verifications:")
        print(f"  {'pending':<14} {summary.get('verification_pending', 0)}")
        if summary.get("verification_current"):
            print("  current:")
            for decision, count in sorted(summary["verification_current"].items()):
                print(f"    {decision:<12} {count}")
        if summary.get("verifications"):
            print("  records:")
            for decision, count in sorted(summary["verifications"].items()):
                print(f"    {decision:<12} {count}")
    return 0


def filtered_active_leases(summary: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    leases = list(summary.get("active_leases") or [])
    if args.stale_heartbeat_seconds is not None:
        leases = [
            lease
            for lease in leases
            if lease.get("heartbeat_age_seconds") is not None
            and lease["heartbeat_age_seconds"] >= args.stale_heartbeat_seconds
        ]
    if args.expiring_within_seconds is not None:
        leases = [
            lease
            for lease in leases
            if lease.get("expires_in_seconds") is not None
            and lease["expires_in_seconds"] <= args.expiring_within_seconds
        ]
    return leases


def cmd_leases(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        summary = runtime.status_summary(args.run_id)
    finally:
        runtime.close()
    leases = filtered_active_leases(summary, args)
    payload = {
        "run_id": args.run_id,
        "count": len(leases),
        "active_leases": leases,
    }
    exit_code = 1 if args.fail_on_match and leases else 0
    if args.json:
        print(json.dumps(payload, indent=2))
        return exit_code
    if not leases:
        print("No active leases matched.")
        return exit_code
    for lease in leases:
        print(
            f"{lease['lease_id']} task={lease['task_id']} lane={lease['lane']} slot={lease['slot_id']} "
            f"age={lease['age_seconds']}s heartbeat_age={lease['heartbeat_age_seconds']}s "
            f"expires_in={lease['expires_in_seconds']}s"
        )
    return exit_code


def cmd_expire_leases(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        expired = runtime.expire_stale_leases(args.run_id, lane=args.lane)
    finally:
        runtime.close()
    payload = {
        "run_id": args.run_id,
        "lane": args.lane,
        "expired_count": len(expired),
        "expired_leases": expired,
    }
    exit_code = 1 if args.fail_on_expired and expired else 0
    if args.json:
        print(json.dumps(payload, indent=2))
        return exit_code
    if not expired:
        print("No expired active leases found.")
        return exit_code
    for lease in expired:
        print(
            f"EXPIRED {lease['lease_id']} task={lease['task_id']} lane={lease['lane']} "
            f"slot={lease['slot_id']} previous_expires_at={lease['expires_at']}"
        )
    return exit_code


def cmd_cancel_attempt(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        canceled = runtime.cancel_attempt(
            args.run_id,
            args.attempt_id,
            diagnostics=args.diagnostics,
            retryable=not args.no_retry,
        )
    finally:
        runtime.close()
    payload = {
        "run_id": args.run_id,
        "attempt_id": args.attempt_id,
        "canceled": canceled is not None,
        "attempt": canceled,
    }
    exit_code = 1 if args.fail_on_canceled and canceled is not None else 0
    if args.json:
        print(json.dumps(payload, indent=2))
        return exit_code
    if canceled is None:
        print("No cancelable attempt found.")
        return exit_code
    print(
        f"CANCELED {args.attempt_id} task={canceled['task_id']} lane={canceled['lane']} "
        f"next_task_status={canceled['next_task_status']}"
    )
    return exit_code


def cmd_attempts(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        attempts = runtime.attempt_records(
            args.run_id,
            task_id=args.task_id,
            status=args.status,
            failure_class=args.failure_class,
            limit=args.limit,
        )
    finally:
        runtime.close()
    payload = {
        "run_id": args.run_id,
        "count": len(attempts),
        "attempts": attempts,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    if not attempts:
        print("No attempts found.")
        return 0
    for attempt in attempts:
        failure = f" failure_class={attempt['failure_class']}" if attempt.get("failure_class") else ""
        diagnostics = f": {attempt['diagnostics']}" if attempt.get("diagnostics") else ""
        print(
            f"{attempt['status'].upper()} {attempt['task_id']} attempt={attempt['attempt_id']} "
            f"lane={attempt['lane']} slot={attempt['slot_id']}{failure}{diagnostics}"
        )
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        tasks = runtime.task_records(
            args.run_id,
            task_id=args.task_id,
            lane=args.lane,
            status=args.status,
            limit=args.limit,
        )
    finally:
        runtime.close()
    payload = {
        "run_id": args.run_id,
        "count": len(tasks),
        "tasks": tasks,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    if not tasks:
        print("No tasks found.")
        return 0
    for task in tasks:
        latest = ""
        if task.get("latest_attempt_id"):
            latest = f" latest_attempt={task['latest_attempt_id']} latest_status={task['latest_attempt_status']}"
            if task.get("latest_failure_class"):
                latest += f" latest_failure_class={task['latest_failure_class']}"
        print(
            f"{task['status'].upper()} {task['task_id']} lane={task['lane']} priority={task['priority']} "
            f"deps={len(task['dependencies'])} writes={len(task['write_set'])} attempts={task['attempt_count']}{latest}"
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


def cmd_events(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        events = runtime.recent_events(args.run_id, limit=args.limit, message_type=args.type)
    finally:
        runtime.close()
    if args.json:
        print(json.dumps(events, indent=2))
        return 0
    if not events:
        print("No events found.")
        return 0
    for event in events:
        subject = []
        if event.get("lane"):
            subject.append(f"lane={event['lane']}")
        if event.get("task_id"):
            subject.append(f"task={event['task_id']}")
        if event.get("slot_id"):
            subject.append(f"slot={event['slot_id']}")
        payload = event.get("payload") or {}
        payload_text = ""
        if payload:
            payload_text = " " + json.dumps(payload, sort_keys=True)
        subject_text = " ".join(subject)
        if subject_text:
            subject_text = " " + subject_text
        print(f"{event['sequence_no']:>5} {event['timestamp']} {event['message_type']}{subject_text}{payload_text}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        outcomes = verify_completed_attempts(
            runtime,
            args.run_id,
            task_id=args.task_id,
            verifier=args.verifier,
            artifact_root=args.artifact_root,
            require_artifacts=args.require_artifacts,
            validation_command=args.validation_command,
            include_verified=args.include_verified,
        )
    finally:
        runtime.close()
    payload = [
        {
            "task_id": outcome.task_id,
            "attempt_id": outcome.attempt_id,
            "decision": outcome.decision,
            "verification_id": outcome.verification_id,
            "diagnostics": outcome.diagnostics,
            "evidence": outcome.evidence,
        }
        for outcome in outcomes
    ]
    if args.json:
        print(json.dumps(payload, indent=2))
        return 1 if any(outcome.decision == "rejected" for outcome in outcomes) else 0
    if not outcomes:
        print("No completed attempts pending verification.")
        return 0
    for outcome in outcomes:
        print(
            f"{outcome.decision.upper()} {outcome.task_id} "
            f"attempt={outcome.attempt_id} verification={outcome.verification_id}: {outcome.diagnostics}"
        )
    return 1 if any(outcome.decision == "rejected" for outcome in outcomes) else 0


def cmd_verifications(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        records = runtime.verification_records(
            args.run_id,
            task_id=args.task_id,
            decision=args.decision,
            limit=args.limit,
            latest=args.latest,
        )
    finally:
        runtime.close()
    if args.json:
        print(json.dumps(records, indent=2))
        return 0
    if not records:
        print("No verification records found.")
        return 0
    for record in records:
        diagnostics = record.get("diagnostics") or ""
        suffix = f": {diagnostics}" if diagnostics else ""
        print(
            f"{record['verified_at']} {record['decision'].upper()} {record['task_id']} "
            f"attempt={record['attempt_id']} verification={record['verification_id']} "
            f"verifier={record['verifier']}{suffix}"
        )
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        preview = reconciliation_preview(
            runtime,
            args.run_id,
            task_id=args.task_id,
            decision=args.decision,
            hint=args.hint,
            limit=args.limit,
        )
    finally:
        runtime.close()
    fail_on_hints = set(args.fail_on_hint or [])
    exit_code = 1 if any(preview["controller_hints"].get(hint, 0) for hint in fail_on_hints) else 0
    if args.fail_on_pending and preview["pending_verification"]:
        exit_code = 1
    if args.json:
        print(json.dumps(preview, indent=2))
        return exit_code
    print(f"Reconciliation preview for run {preview['run_id']}")
    print(f"Pending verification: {preview['pending_verification']}")
    if not preview["actions"]:
        print("No current verification records found.")
        return exit_code
    for action in preview["actions"]:
        diagnostics = action.get("diagnostics") or ""
        suffix = f": {diagnostics}" if diagnostics else ""
        print(
            f"  {action['decision'].upper()} {action['task_id']} "
            f"attempt={action['attempt_id']} hint={action['controller_hint']} "
            f"verification={action['verification_id']}{suffix}"
        )
    return exit_code


def cmd_worker(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    account_gate = make_account_gate(args)
    try:
        manager = WorkerManager(
            runtime,
            args.run_id,
            args.lane,
            slots=args.slots,
            dry_run=args.dry_run,
            agent_command=args.agent_command,
            agent_preset=args.agent,
            codex_model=args.codex_model,
            codex_sandbox=args.codex_sandbox,
            codex_profile=args.codex_profile,
            codex_extra_args=args.codex_extra_args,
            tick_interval=args.tick_interval,
            use_worktrees=args.worktrees,
            repo_root=args.repo_root,
            worktree_root=args.worktree_root,
            account_gate=account_gate,
            account_gated_lanes=parse_csv(args.account_gated_lanes),
            account_gate_ttl_sec=args.account_gate_ttl,
            parallel=args.parallel,
        )
        manager.run(once=args.once, max_ticks=args.max_ticks, exit_when_idle=args.exit_when_idle)
    finally:
        if account_gate is not None:
            account_gate.close()
        runtime.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    account_gate = make_account_gate(args)
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
                agent_preset=args.agent,
                codex_model=args.codex_model,
                codex_sandbox=args.codex_sandbox,
                codex_profile=args.codex_profile,
                codex_extra_args=args.codex_extra_args,
                tick_interval=args.tick_interval,
                use_worktrees=args.worktrees,
                repo_root=args.repo_root,
                worktree_root=args.worktree_root,
                account_gate=account_gate,
                account_gated_lanes=parse_csv(args.account_gated_lanes),
                account_gate_ttl_sec=args.account_gate_ttl,
                parallel=args.parallel,
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
            if args.exit_when_idle and all(manager.is_idle() for manager in managers):
                break
            if args.once or (args.max_ticks is not None and tick >= args.max_ticks):
                break
            time.sleep(args.tick_interval)
    finally:
        if account_gate is not None:
            account_gate.close()
        runtime.close()
    return 0


def cmd_router(args: argparse.Namespace) -> int:
    router = Router(args.repo_root)
    if args.router_command == "list":
        branches = router.pending_branches(args.run_id)
        print(Router.format_pending(branches, json_output=args.json))
        return 0
    if args.router_command == "plan":
        print(json.dumps(router.merge_plan(args.branch, args.base), indent=2))
        return 0
    if args.router_command == "merge":
        print(json.dumps(router.merge_branch(args.branch, base=args.base, dry_run=args.dry_run), indent=2))
        return 0
    raise ValueError(f"Unknown router command: {args.router_command}")


def cmd_pause(args: argparse.Namespace) -> int:
    return _set_status(args, "paused")


def cmd_resume(args: argparse.Namespace) -> int:
    return _set_status(args, "active")


def cmd_drain(args: argparse.Namespace) -> int:
    return _set_status(args, "draining")


def cmd_account_init(args: argparse.Namespace) -> int:
    gate = make_standalone_account_gate(args)
    try:
        if args.resources_json:
            gate.configure_resources(load_resource_config(args.resources_json), replace=True)
        summary = gate.status_summary()
    finally:
        gate.close()
    print(f"Initialized account gate {summary['account_id']}")
    print(f"Gate database: {summary['db_path']}")
    print("Resources:")
    for resource in summary["resources"]:
        print(f"  {resource['resource']:<16} max={resource['max_slots']}")
    return 0


def cmd_account_status(args: argparse.Namespace) -> int:
    gate = make_standalone_account_gate(args)
    try:
        summary = gate.status_summary()
    finally:
        gate.close()
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"SMI account gate: {summary['account_id']}")
    print(f"Gate database   : {summary['db_path']}")
    print("Resources:")
    for resource in summary["resources"]:
        permits = resource["permits"]
        active = permits.get("active", 0)
        print(f"  {resource['resource']:<16} active={active} max={resource['max_slots']}")
    if summary["active_permits"]:
        print("Active permits:")
        for permit in summary["active_permits"]:
            print(
                f"  {permit['permit_id']} {permit['resource']} "
                f"holder={permit['holder']} expires={permit['expires_at']}"
            )
    return 0


def cmd_account_state(args: argparse.Namespace) -> int:
    gate = make_standalone_account_gate(args)
    try:
        json_path, markdown_path = account_state_paths(gate)
    finally:
        gate.close()
    if args.path:
        print(json_path if args.format == "json" else markdown_path)
        return 0
    path = json_path if args.format == "json" else markdown_path
    if not path.exists():
        print(f"No account state snapshot found at {path}", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def cmd_account_release(args: argparse.Namespace) -> int:
    gate = make_standalone_account_gate(args)
    try:
        released = gate.release(args.permit_id)
    finally:
        gate.close()
    if released:
        print(f"Released permit {args.permit_id}")
        return 0
    print(f"No active permit found for {args.permit_id}")
    return 1


def cmd_account_watch(args: argparse.Namespace) -> int:
    gate = make_standalone_account_gate(args)
    try:
        if args.resources_json:
            gate.configure_resources(load_resource_config(args.resources_json), replace=True)
        runner = (
            AccountClusterRunner(gate, cluster_config=args.cluster_config)
            if args.touch_sessions or args.queue_snapshot
            else None
        )
        touch_clusters = parse_csv(args.clusters)
        if runner is not None and not touch_clusters:
            touch_clusters = set((runner.config.get("clusters") or {}).keys())
        tick = 0
        while True:
            tick += 1
            expired = gate.reap_expired()
            summary = gate.status_summary()
            touch_results = []
            if runner is not None:
                if args.touch_sessions:
                    touch_results = runner.touch_sessions(
                        sorted(touch_clusters),
                        open_if_missing=args.open_if_missing,
                        touch_command=args.touch_command,
                    )
                queue_results = (
                    runner.queue_snapshot(sorted(touch_clusters), limit=args.queue_limit) if args.queue_snapshot else []
                )
            else:
                queue_results = []
            state_paths = write_account_state(gate, summary, touch_results=touch_results, queue_results=queue_results)
            if args.json:
                print(
                    json.dumps(
                        {
                            "tick": tick,
                            "expired": expired,
                            "state_file": str(state_paths[0]),
                            "touch_results": touch_results,
                            "queue_results": queue_results,
                            **summary,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            else:
                resource_bits = []
                for resource in summary["resources"]:
                    active = resource["permits"].get("active", 0)
                    resource_bits.append(f"{resource['resource']}={active}/{resource['max_slots']}")
                print(
                    f"tick={tick} account={summary['account_id']} expired={expired} "
                    + " ".join(resource_bits)
                    + f" state={state_paths[0]}",
                    flush=True,
                )
                for result in touch_results:
                    status = "ok" if result.get("ok") else "failed"
                    if result.get("skipped"):
                        status = "skipped"
                    print(f"  touch {result.get('cluster')}: {status}", flush=True)
                for result in queue_results:
                    status = "ok" if result.get("ok") else "failed"
                    if result.get("skipped"):
                        status = "skipped"
                    parsed = result.get("parsed") or {}
                    print(
                        f"  queue {result.get('cluster')}: {status} "
                        f"squeue={len(parsed.get('squeue', []))} sacct={len(parsed.get('sacct', []))}",
                        flush=True,
                    )
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        gate.close()
    return 0


def cmd_account_cluster_smoke(args: argparse.Namespace) -> int:
    gate = make_standalone_account_gate(args)
    try:
        if args.resources_json:
            gate.configure_resources(load_resource_config(args.resources_json), replace=True)
    finally:
        gate.close()

    clusters = sorted(parse_csv(args.clusters))
    if not clusters:
        raise ValueError("--clusters must name at least one cluster")
    if args.serial or len(clusters) == 1:
        results = [run_account_cluster_smoke_one(args, cluster_name) for cluster_name in clusters]
    else:
        workers = args.max_workers or len(clusters)
        results_by_cluster = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(run_account_cluster_smoke_one, args, cluster_name): cluster_name
                for cluster_name in clusters
            }
            for future in concurrent.futures.as_completed(futures):
                cluster_name = futures[future]
                try:
                    results_by_cluster[cluster_name] = future.result()
                except Exception as exc:
                    results_by_cluster[cluster_name] = {"ok": False, "cluster": cluster_name, "error": str(exc)}
        results = [results_by_cluster[cluster_name] for cluster_name in clusters]

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            status = "ok" if result["ok"] else "failed"
            if "error" in result:
                print(f"{status} {result['cluster']} error={result['error']}")
                continue
            print(
                f"{status} {result['cluster']} job={result['job_id']} "
                f"state={result['final_state']['state']} local={result['local_dir']}"
            )
    return 0 if all(result["ok"] for result in results) else 1


def run_account_cluster_smoke_one(args: argparse.Namespace, cluster_name: str) -> dict[str, Any]:
    gate = make_standalone_account_gate(args)
    try:
        runner = AccountClusterRunner(gate, cluster_config=args.cluster_config)
        return runner.run_smoke_job(
            cluster_name,
            label=args.label,
            local_output_dir=args.local_output_dir,
            remote_dir=args.remote_dir,
            profile=args.profile,
            sbatch_args=args.sbatch_args or [],
            poll_interval=args.poll_interval,
            timeout_sec=args.timeout,
            open_if_missing=args.open_if_missing,
        )
    finally:
        gate.close()


def cmd_agent_cluster_smoke(args: argparse.Namespace) -> int:
    raw_prompt = sys.stdin.read()
    try:
        payload = json.loads(raw_prompt)
    except json.JSONDecodeError as exc:
        print(f"error: cluster-smoke agent prompt must be JSON: {exc}", file=sys.stderr)
        return 2

    gate = make_agent_account_gate(args)
    try:
        runner = AccountClusterRunner(gate, cluster_config=args.cluster_config)
        result = runner.run_smoke_job(
            payload["cluster"],
            label=payload.get("label", args.label),
            local_output_dir=payload.get("local_output_dir", args.local_output_dir),
            remote_dir=payload.get("remote_dir"),
            profile=payload.get("profile", args.profile),
            sbatch_args=payload.get("sbatch_args", args.sbatch_args or []),
            poll_interval=float(payload.get("poll_interval", args.poll_interval)),
            timeout_sec=float(payload.get("timeout", args.timeout)),
            open_if_missing=bool(payload.get("open_if_missing", args.open_if_missing)),
            run_id=os.environ.get("SMI_RUN_ID"),
            task_id=os.environ.get("SMI_TASK_ID"),
            slot_id=os.environ.get("SMI_SLOT_ID"),
        )
    finally:
        gate.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def _set_status(args: argparse.Namespace, status: str) -> int:
    runtime = runtime_for(args.run_root, args.run_id)
    try:
        runtime.set_run_status(args.run_id, status)
    finally:
        runtime.close()
    print(f"Run {args.run_id}: {status}")
    return 0


def parse_csv(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def make_account_gate(args: argparse.Namespace) -> AccountGate | None:
    account_id = getattr(args, "account_gate_id", None)
    if not account_id:
        return None
    root = getattr(args, "account_gate_root", None) or args.run_root
    return AccountGate(account_id, account_gate_path(root, account_id))


def make_standalone_account_gate(args: argparse.Namespace) -> AccountGate:
    root = args.account_root or args.run_root
    return AccountGate(args.account_id, account_gate_path(root, args.account_id))


def make_agent_account_gate(args: argparse.Namespace) -> AccountGate:
    root = args.account_root or args.run_root
    return AccountGate(args.account_id, account_gate_path(root, args.account_id))


def account_state_paths(gate: AccountGate) -> tuple[Path, Path]:
    directory = gate.db_path.parent
    return directory / "account_state.json", directory / "account_state.md"


def write_account_state(
    gate: AccountGate,
    summary: dict[str, Any],
    *,
    touch_results: list[dict[str, Any]],
    queue_results: list[dict[str, Any]],
) -> tuple[Path, Path]:
    json_path, markdown_path = account_state_paths(gate)
    state = {
        "updated_at": utc_now(),
        "account_id": summary["account_id"],
        "db_path": summary["db_path"],
        "resources": summary["resources"],
        "active_permits": summary["active_permits"],
        "touch_results": touch_results,
        "queue_results": queue_results,
    }
    json_tmp = json_path.with_suffix(".json.tmp")
    json_tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    json_tmp.replace(json_path)

    lines = [
        f"# SMI Account State: {summary['account_id']}",
        "",
        f"Updated: {state['updated_at']}",
        "",
        "## Lanes",
        "",
        "| resource | active | max |",
        "| --- | ---: | ---: |",
    ]
    for resource in summary["resources"]:
        lines.append(
            f"| {resource['resource']} | {resource['permits'].get('active', 0)} | {resource['max_slots']} |"
        )
    if summary["active_permits"]:
        lines.extend(["", "## Active Permits", ""])
        for permit in summary["active_permits"]:
            lines.append(
                f"- `{permit['permit_id']}` `{permit['resource']}` holder=`{permit['holder']}` expires=`{permit['expires_at']}`"
            )
    if queue_results:
        lines.extend(["", "## Cluster Queues", ""])
        for result in queue_results:
            parsed = result.get("parsed") or {}
            lines.append(f"### {result.get('cluster')}")
            if result.get("skipped"):
                lines.append(f"- skipped: {result.get('error')}")
                continue
            if not result.get("ok"):
                lines.append("- queue snapshot failed")
                continue
            squeue = parsed.get("squeue", [])
            sacct = parsed.get("sacct", [])
            lines.append(f"- squeue rows: {len(squeue)}")
            lines.append(f"- sacct rows: {len(sacct)}")
            for row in squeue[:10]:
                lines.append(f"- queued/running `{row.get('job_id')}` `{row.get('state')}` `{row.get('name')}`")
            for row in sacct[-10:]:
                lines.append(f"- recent `{row.get('job_id')}` `{row.get('state')}` `{row.get('name')}`")
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    markdown_tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    markdown_tmp.replace(markdown_path)
    return json_path, markdown_path


def add_agent_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", choices=SUPPORTED_AGENT_PRESETS, help="Named agent preset for workers.")
    parser.add_argument("--agent-command", help="Explicit command to launch; overrides --agent.")
    parser.add_argument("--codex-model", help="Model passed to `codex exec --model`.")
    parser.add_argument("--codex-sandbox", help="Sandbox passed to `codex exec --sandbox`.")
    parser.add_argument("--codex-profile", help="Profile passed to `codex exec --profile`.")
    parser.add_argument("--codex-extra-args", help="Additional shell-style arguments passed to `codex exec`.")


def add_account_gate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account-gate-id", help="Shared account gate id, for example drac-fouquet.")
    parser.add_argument("--account-gate-root", help="Directory holding SMI_account_* gate databases.")
    parser.add_argument(
        "--account-gated-lanes",
        default="remote_transfer,remote_submit,remote_monitor,remote_cluster",
        help="Comma-separated lanes that must acquire shared account permits.",
    )
    parser.add_argument("--account-gate-ttl", type=int, default=14_400, help="Account permit TTL in seconds.")


def add_account_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--account-root", help="Directory holding SMI_account_* state.")


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

    p = sub.add_parser("leases", help="Inspect active leases.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--stale-heartbeat-seconds", type=int, help="Show active leases whose heartbeat age is at least this many seconds.")
    p.add_argument("--expiring-within-seconds", type=int, help="Show active leases expiring within this many seconds.")
    p.add_argument("--fail-on-match", action="store_true", help="Exit nonzero if any active lease matches the filters.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_leases)

    p = sub.add_parser("expire-leases", help="Expire active leases whose deadlines have passed.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--lane", help="Only expire leases in this lane.")
    p.add_argument("--fail-on-expired", action="store_true", help="Exit nonzero if any lease is expired.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_expire_leases)

    p = sub.add_parser("cancel-attempt", help="Cancel one pending or running attempt.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--attempt-id", required=True)
    p.add_argument("--diagnostics", default="Canceled by operator.")
    p.add_argument("--no-retry", action="store_true", help="Mark the task rejected instead of retry_ready.")
    p.add_argument("--fail-on-canceled", action="store_true", help="Exit nonzero when the attempt is canceled.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_cancel_attempt)

    p = sub.add_parser("attempts", help="Show attempt records.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", help="Filter by task.")
    p.add_argument("--status", choices=("pending", "running", "completed", "failed"), help="Filter by attempt status.")
    p.add_argument("--failure-class", help="Filter by failure class.")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_attempts)

    p = sub.add_parser("tasks", help="Show task records.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", help="Filter by task.")
    p.add_argument("--lane", help="Filter by lane.")
    p.add_argument(
        "--status",
        choices=("ready", "retry_ready", "blocked", "leased", "running", "completed", "rejected"),
        help="Filter by task status.",
    )
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("seed", help="Seed tasks from a JSON spec.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--spec", required=True)
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("orders", help="Process pending hot-folder orders once.")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("events", help="Show recent SMI event records.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--type", help="Filter by event message_type.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("verify", help="Record neutral verification decisions for completed attempts.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", help="Verify only one task.")
    p.add_argument("--verifier", default="neutral")
    p.add_argument("--artifact-root", help="Root used for relative expected artifact checks. Defaults to run dir.")
    p.add_argument("--require-artifacts", action="store_true", help="Hold tasks when declared artifacts are missing.")
    p.add_argument("--validation-command", help="Optional command that must exit 0 for each verified attempt.")
    p.add_argument("--include-verified", action="store_true", help="Create another verification for already verified attempts.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("verifications", help="Show verification records.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", help="Filter by task.")
    p.add_argument("--decision", choices=("accepted", "rejected", "held"), help="Filter by decision.")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--latest", action="store_true", help="Show only the latest verification record for each attempt.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verifications)

    p = sub.add_parser("reconcile", help="Preview controller actions from current verification records.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", help="Filter by task.")
    p.add_argument("--decision", choices=("accepted", "rejected", "held"), help="Filter by decision.")
    p.add_argument("--hint", choices=("use_result", "exclude_result", "review_result"), help="Filter by controller hint.")
    p.add_argument(
        "--fail-on-hint",
        action="append",
        choices=("use_result", "exclude_result", "review_result"),
        help="Exit nonzero if the preview contains this controller hint. Can be repeated.",
    )
    p.add_argument("--fail-on-pending", action="store_true", help="Exit nonzero if completed attempts are pending verification.")
    p.add_argument("--limit", type=int, default=1000)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("worker", help="Run one lane worker manager.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--lane", default="fast_local")
    p.add_argument("--slots", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    add_agent_args(p)
    p.add_argument("--tick-interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--max-ticks", type=int)
    p.add_argument("--parallel", action="store_true", help="Launch and supervise subprocesses concurrently per slot.")
    p.add_argument("--exit-when-idle", action="store_true", help="Exit when this lane has no ready/running work.")
    p.add_argument("--worktrees", action="store_true", help="Run workers in per-slot git worktrees.")
    p.add_argument("--repo-root", help="Repository root for worktree isolation.")
    p.add_argument("--worktree-root", help="Directory for SMI worktrees.")
    add_account_gate_args(p)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("run", help="Run order watcher and workers in one loop.")
    p.add_argument("--run-id", required=True)
    p.add_argument("--lanes", default="fast_local")
    p.add_argument("--slots", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    add_agent_args(p)
    p.add_argument("--tick-interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--max-ticks", type=int)
    p.add_argument("--parallel", action="store_true", help="Launch and supervise subprocesses concurrently per slot.")
    p.add_argument("--exit-when-idle", action="store_true", help="Exit when selected lanes have no ready/running work.")
    p.add_argument("--worktrees", action="store_true", help="Run workers in per-slot git worktrees.")
    p.add_argument("--repo-root", help="Repository root for worktree isolation.")
    p.add_argument("--worktree-root", help="Directory for SMI worktrees.")
    add_account_gate_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("router", help="Review and merge SMI worktree branches.")
    p.add_argument("--repo-root", default=".")
    router_sub = p.add_subparsers(dest="router_command", required=True)
    p_list = router_sub.add_parser("list", help="List pending SMI branches.")
    p_list.add_argument("--run-id")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_router)
    p_plan = router_sub.add_parser("plan", help="Show a merge plan for a branch.")
    p_plan.add_argument("branch")
    p_plan.add_argument("--base", default="HEAD")
    p_plan.set_defaults(func=cmd_router)
    p_merge = router_sub.add_parser("merge", help="Cherry-pick a reviewed SMI branch.")
    p_merge.add_argument("branch")
    p_merge.add_argument("--base", default="HEAD")
    p_merge.add_argument("--dry-run", action="store_true")
    p_merge.set_defaults(func=cmd_router)

    p = sub.add_parser("pause")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("resume")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("drain")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_drain)

    p = sub.add_parser("account", help="Manage a shared account gate for multiple SMI project runs.")
    account_sub = p.add_subparsers(dest="account_command", required=True)
    p_init = account_sub.add_parser("init", help="Initialize or update account gate resources.")
    add_account_identity_args(p_init)
    p_init.add_argument("--resources-json", help="Resource config JSON; lane-style max_slots files are accepted.")
    p_init.set_defaults(func=cmd_account_init)
    p_status = account_sub.add_parser("status", help="Show account gate permit usage.")
    add_account_identity_args(p_status)
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_account_status)
    p_state = account_sub.add_parser("state", help="Read the visible account state snapshot.")
    add_account_identity_args(p_state)
    p_state.add_argument("--format", choices=("json", "markdown"), default="json")
    p_state.add_argument("--path", action="store_true", help="Print the state snapshot path only.")
    p_state.set_defaults(func=cmd_account_state)
    p_release = account_sub.add_parser("release", help="Manually release a stuck active permit.")
    add_account_identity_args(p_release)
    p_release.add_argument("--permit-id", required=True)
    p_release.set_defaults(func=cmd_account_release)
    p_watch = account_sub.add_parser("watch", help="Run an always-on account gate monitor.")
    add_account_identity_args(p_watch)
    p_watch.add_argument("--resources-json", help="Resource config JSON to apply before watching.")
    p_watch.add_argument("--interval", type=float, default=60.0, help="Seconds between account gate monitor ticks.")
    p_watch.add_argument("--once", action="store_true", help="Run one monitor tick and exit.")
    p_watch.add_argument("--json", action="store_true", help="Emit one JSON object per monitor tick.")
    p_watch.add_argument("--touch-sessions", action="store_true", help="Touch configured cluster SSH sessions each tick.")
    p_watch.add_argument("--queue-snapshot", action="store_true", help="Refresh local squeue/sacct snapshots each tick.")
    p_watch.add_argument("--cluster-config", default="config/clusters.json", help="Cluster config used for session touches.")
    p_watch.add_argument("--clusters", help="Comma-separated clusters to touch. Defaults to all configured clusters.")
    p_watch.add_argument("--queue-limit", type=int, default=40, help="Maximum squeue/sacct rows per cluster snapshot.")
    p_watch.add_argument("--touch-command", default="true", help="Remote command used to touch sessions.")
    p_watch.add_argument("--open-if-missing", action="store_true", help="Open SSH ControlMaster sessions when missing.")
    p_watch.set_defaults(func=cmd_account_watch)

    p_smoke = account_sub.add_parser("cluster-smoke", help="Run and harvest small SLURM smoke jobs through the account gate.")
    add_account_identity_args(p_smoke)
    p_smoke.add_argument("--resources-json", help="Resource config JSON to apply before running.")
    p_smoke.add_argument("--cluster-config", default="config/clusters.json")
    p_smoke.add_argument("--clusters", required=True, help="Comma-separated cluster names.")
    p_smoke.add_argument("--label", required=True, help="Remote/local label for this smoke run.")
    p_smoke.add_argument("--local-output-dir", required=True, help="Local directory for harvested outputs and summaries.")
    p_smoke.add_argument("--remote-dir", help="Remote base directory. Defaults to cluster remote_project_dir.")
    p_smoke.add_argument("--profile", help="Cluster sbatch profile.")
    p_smoke.add_argument("--sbatch-args", nargs="*", help="Additional sbatch flags.")
    p_smoke.add_argument("--poll-interval", type=float, default=10.0)
    p_smoke.add_argument("--timeout", type=float, default=900.0)
    p_smoke.add_argument("--open-if-missing", action="store_true")
    p_smoke.add_argument("--serial", action="store_true", help="Run clusters one at a time instead of concurrently.")
    p_smoke.add_argument("--max-workers", type=int, help="Maximum concurrent cluster workflows.")
    p_smoke.add_argument("--json", action="store_true")
    p_smoke.set_defaults(func=cmd_account_cluster_smoke)

    p = sub.add_parser("agent", help="Small deterministic agents for SMI worker integration tests.")
    agent_sub = p.add_subparsers(dest="agent_command", required=True)
    p_agent_smoke = agent_sub.add_parser("cluster-smoke", help="Read a JSON prompt and run a cluster smoke job.")
    p_agent_smoke.add_argument("--account-id", required=True)
    p_agent_smoke.add_argument("--account-root", help="Directory holding SMI_account_* state.")
    p_agent_smoke.add_argument("--cluster-config", default="config/clusters.json")
    p_agent_smoke.add_argument("--label", default="smi-agent-smoke")
    p_agent_smoke.add_argument("--local-output-dir", default="runs/smi-agent-smoke")
    p_agent_smoke.add_argument("--profile", help="Cluster sbatch profile.")
    p_agent_smoke.add_argument("--sbatch-args", nargs="*", help="Additional sbatch flags.")
    p_agent_smoke.add_argument("--poll-interval", type=float, default=10.0)
    p_agent_smoke.add_argument("--timeout", type=float, default=900.0)
    p_agent_smoke.add_argument("--open-if-missing", action="store_true")
    p_agent_smoke.set_defaults(func=cmd_agent_cluster_smoke)
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

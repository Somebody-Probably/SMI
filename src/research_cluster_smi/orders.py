"""Hot-folder order processing for the generic SMI runtime."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .smi_core import SMIRuntime


ORDER_TYPES = {"seed", "cancel", "reprioritize", "pause", "resume", "drain", "lane_config"}


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


@dataclass
class OrderResult:
    filename: str
    order_type: str
    success: bool
    message: str
    details: dict[str, Any]


class OrderWatcher:
    """Process JSON order files from a run's orders directory."""

    def __init__(self, runtime: SMIRuntime, run_id: str, orders_dir: str | Path) -> None:
        self.runtime = runtime
        self.run_id = run_id
        self.orders_dir = Path(orders_dir)
        self.processed_dir = self.orders_dir / "processed"
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> list[OrderResult]:
        results: list[OrderResult] = []
        for path in sorted(self.orders_dir.glob("*.json"), key=lambda item: item.stat().st_mtime):
            result = self._process(path)
            results.append(result)
            self._archive(path, result)
        return results

    def _process(self, path: Path) -> OrderResult:
        try:
            order = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(order, dict):
                return OrderResult(path.name, "unknown", False, "Order must be a JSON object.", {})
            order_type = str(order.get("order_type", "")).lower()
            if order_type not in ORDER_TYPES:
                return OrderResult(path.name, order_type, False, f"Unsupported order_type: {order_type}", {})
            payload = order.get("payload") or {}
            handler = getattr(self, f"_handle_{order_type}")
            return handler(path.name, payload)
        except json.JSONDecodeError as exc:
            return OrderResult(path.name, "unknown", False, f"Invalid JSON: {exc}", {})
        except Exception as exc:
            return OrderResult(path.name, "unknown", False, f"Processing error: {exc}", {})

    def _archive(self, path: Path, result: OrderResult) -> None:
        status = "ok" if result.success else "err"
        destination = self.processed_dir / f"{timestamp()}_{status}_{path.name}"
        shutil.move(str(path), str(destination))

    def _handle_seed(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        tasks = payload.get("tasks") or []
        if not tasks:
            return OrderResult(filename, "seed", False, "No tasks supplied.", {})
        seeded: list[str] = []
        errors: list[str] = []
        prompts_dir = self.runtime.run_dir / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        for index, task in enumerate(tasks):
            task_id = task.get("id") or task.get("task_id") or f"task-{index:03d}"
            try:
                prompt_path = task.get("prompt_path")
                if task.get("prompt") and not prompt_path:
                    prompt_file = prompts_dir / f"{task_id}.md"
                    prompt_file.write_text(str(task["prompt"]), encoding="utf-8")
                    prompt_path = str(prompt_file)
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
                seeded.append(
                    self.runtime.seed_task(
                        self.run_id,
                        task.get("lane", "fast_local"),
                        task_id=task_id,
                        priority=int(task.get("priority", 100)),
                        dependencies=task.get("dependencies") or [],
                        write_set=task.get("write_set") or [],
                        prompt_path=prompt_path,
                        metadata=metadata,
                    )
                )
            except Exception as exc:
                errors.append(f"{task_id}: {exc}")
        self.runtime.publish_event("order.seed", self.run_id, payload={"seeded": seeded, "errors": errors})
        success = bool(seeded)
        message = f"Seeded {len(seeded)} task(s)"
        if errors:
            message += f"; {len(errors)} error(s)"
        return OrderResult(filename, "seed", success, message, {"seeded": seeded, "errors": errors})

    def _handle_cancel(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        task_ids = payload.get("task_ids") or []
        reason = payload.get("reason", "cancelled by order")
        if not task_ids:
            return OrderResult(filename, "cancel", False, "No task_ids supplied.", {})
        with self.runtime.conn:
            for task_id in task_ids:
                self.runtime.conn.execute(
                    "UPDATE tasks SET status='cancelled', updated_at=? WHERE run_id=? AND task_id=?",
                    (datetime.now(timezone.utc).isoformat(), self.run_id, task_id),
                )
        self.runtime.publish_event("order.cancel", self.run_id, payload={"task_ids": task_ids, "reason": reason})
        return OrderResult(filename, "cancel", True, f"Cancelled {len(task_ids)} task(s).", {})

    def _handle_reprioritize(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        changes = payload.get("changes") or []
        updated = 0
        with self.runtime.conn:
            for change in changes:
                task_id = change.get("task_id")
                priority = change.get("priority")
                if task_id is None or priority is None:
                    continue
                updated += self.runtime.conn.execute(
                    "UPDATE tasks SET priority=?, updated_at=? WHERE run_id=? AND task_id=?",
                    (int(priority), datetime.now(timezone.utc).isoformat(), self.run_id, task_id),
                ).rowcount
        self.runtime.publish_event("order.reprioritize", self.run_id, payload={"updated": updated})
        return OrderResult(filename, "reprioritize", True, f"Updated {updated} task(s).", {})

    def _handle_pause(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        self.runtime.set_run_status(self.run_id, "paused")
        return OrderResult(filename, "pause", True, "Run paused.", {})

    def _handle_resume(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        self.runtime.set_run_status(self.run_id, "active")
        return OrderResult(filename, "resume", True, "Run resumed.", {})

    def _handle_drain(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        self.runtime.set_run_status(self.run_id, "draining")
        return OrderResult(filename, "drain", True, "Run draining.", {})

    def _handle_lane_config(self, filename: str, payload: dict[str, Any]) -> OrderResult:
        lanes = payload.get("lanes") or {}
        updated = []
        with self.runtime.conn:
            for lane, config in lanes.items():
                if "max_slots" in config:
                    self.runtime.conn.execute(
                        "UPDATE lanes SET max_slots=? WHERE run_id=? AND lane=?",
                        (int(config["max_slots"]), self.run_id, lane),
                    )
                    updated.append(lane)
                if "admission_state" in config:
                    self.runtime.conn.execute(
                        "UPDATE lanes SET admission_state=? WHERE run_id=? AND lane=?",
                        (config["admission_state"], self.run_id, lane),
                    )
        self.runtime.publish_event("order.lane_config", self.run_id, payload={"updated": updated})
        return OrderResult(filename, "lane_config", True, f"Updated {len(set(updated))} lane(s).", {})


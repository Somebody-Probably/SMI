"""Small generic SMI runtime.

The runtime is intentionally plain SQLite plus filesystem artifacts. It is
not QFF-specific: tasks carry prompts, write sets, priorities, dependencies,
and metadata; workers consume them through lanes and slots.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LANES: dict[str, dict[str, Any]] = {
    "fast_local": {"max_slots": 2, "description": "Short local agent tasks."},
    "heavy_local": {"max_slots": 1, "description": "Longer local agent tasks."},
    "verify_local": {"max_slots": 1, "description": "Local validation and review."},
    "remote_cluster": {"max_slots": 1, "description": "Placeholder for remote cluster validation."},
}


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS lanes (
    run_id TEXT NOT NULL,
    lane TEXT NOT NULL,
    max_slots INTEGER NOT NULL DEFAULT 1,
    admission_state TEXT NOT NULL DEFAULT 'open',
    backpressure TEXT NOT NULL DEFAULT 'none',
    config_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, lane),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS slots (
    run_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    lane TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    current_lease_id TEXT,
    last_heartbeat_at TEXT,
    heartbeat_timeout_sec INTEGER NOT NULL DEFAULT 300,
    PRIMARY KEY (run_id, slot_id),
    FOREIGN KEY (run_id, lane) REFERENCES lanes(run_id, lane)
);

CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    lane TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    priority INTEGER NOT NULL DEFAULT 100,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    write_set_json TEXT NOT NULL DEFAULT '[]',
    prompt_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id, lane) REFERENCES lanes(run_id, lane)
);

CREATE TABLE IF NOT EXISTS attempts (
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    slot_id TEXT,
    lease_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    enqueued_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    failure_class TEXT,
    diagnostics TEXT,
    PRIMARY KEY (run_id, attempt_id),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id)
);

CREATE TABLE IF NOT EXISTS leases (
    run_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (run_id, lease_id),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id)
);

CREATE TABLE IF NOT EXISTS events (
    sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    lane TEXT,
    task_id TEXT,
    attempt_id TEXT,
    lease_id TEXT,
    slot_id TEXT,
    controller_id TEXT,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS verifications (
    run_id TEXT NOT NULL,
    verification_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    verifier TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    diagnostics TEXT,
    PRIMARY KEY (run_id, verification_id),
    FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, task_id),
    FOREIGN KEY (run_id, attempt_id) REFERENCES attempts(run_id, attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_ready ON tasks(run_id, lane, status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_slots_idle ON slots(run_id, lane, status);
CREATE INDEX IF NOT EXISTS idx_leases_active ON leases(run_id, status);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_verifications_run ON verifications(run_id, task_id, attempt_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_deadline(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def elapsed_seconds(now: datetime, then_value: str | None) -> int | None:
    then = parse_utc_timestamp(then_value)
    if then is None:
        return None
    return max(0, int((now - then).total_seconds()))


def seconds_until(now: datetime, deadline_value: str | None) -> int | None:
    deadline = parse_utc_timestamp(deadline_value)
    if deadline is None:
        return None
    return int((deadline - now).total_seconds())


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Assignment:
    task_id: str
    attempt_id: str
    lease_id: str
    slot_id: str
    lane: str
    prompt_path: str | None
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "slot_id": self.slot_id,
            "lane": self.lane,
            "prompt_path": self.prompt_path,
            "metadata": self.metadata,
        }


class SMIRuntime:
    """SQLite-backed SMI runtime."""

    def __init__(
        self,
        db_path: str | Path,
        run_dir: str | Path | None = None,
        *,
        controller_id: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.run_dir = Path(run_dir) if run_dir else self.db_path.parent
        self.controller_id = controller_id or os.environ.get("SMI_CONTROLLER_ID")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_schema_compatibility()
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema_compatibility(self) -> None:
        event_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        if "controller_id" not in event_columns:
            self.conn.execute("ALTER TABLE events ADD COLUMN controller_id TEXT")

    def initialize_run(
        self,
        run_id: str,
        lanes: dict[str, dict[str, Any]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        lanes = lanes or DEFAULT_LANES
        now = utc_now()
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO runs(run_id, created_at, status, config_json) VALUES(?,?,?,?)",
                (run_id, now, "active", json_dumps(config or {})),
            )
            for lane, lane_config in lanes.items():
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO lanes(
                        run_id, lane, max_slots, admission_state, backpressure, config_json
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        lane,
                        int(lane_config.get("max_slots", 1)),
                        lane_config.get("admission_state", "open"),
                        lane_config.get("backpressure", "none"),
                        json_dumps(lane_config),
                    ),
                )
        self.publish_event("run.initialized", run_id, payload={"lanes": sorted(lanes)})

    def publish_event(
        self,
        message_type: str,
        run_id: str,
        *,
        lane: str | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        lease_id: str | None = None,
        slot_id: str | None = None,
        controller_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        event_controller_id = controller_id if controller_id is not None else self.controller_id
        row = (
            str(uuid.uuid4()),
            run_id,
            message_type,
            lane,
            task_id,
            attempt_id,
            lease_id,
            slot_id,
            event_controller_id,
            utc_now(),
            json_dumps(payload or {}),
        )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO events(
                    message_id, run_id, message_type, lane, task_id, attempt_id,
                    lease_id, slot_id, controller_id, timestamp, payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                row,
            )
        event_file = self.run_dir / "events.jsonl"
        record = {
            "message_id": row[0],
            "run_id": run_id,
            "message_type": message_type,
            "lane": lane,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "slot_id": slot_id,
            "controller_id": event_controller_id,
            "timestamp": row[9],
            "payload": payload or {},
        }
        with event_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def register_slot(self, run_id: str, lane: str, slot_id: str, heartbeat_timeout_sec: int = 300) -> None:
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO slots(
                    run_id, slot_id, lane, status, last_heartbeat_at, heartbeat_timeout_sec
                ) VALUES(?,?,?,?,?,?)
                """,
                (run_id, slot_id, lane, "idle", now, heartbeat_timeout_sec),
            )
            self.conn.execute(
                "UPDATE slots SET last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                (now, run_id, slot_id),
            )
        self.publish_event("slot.registered", run_id, lane=lane, slot_id=slot_id)

    def seed_task(
        self,
        run_id: str,
        lane: str,
        *,
        task_id: str,
        priority: int = 100,
        dependencies: Iterable[str] | None = None,
        write_set: Iterable[str] | None = None,
        prompt_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        dependencies_list = list(dependencies or [])
        status = "blocked" if dependencies_list else "ready"
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO tasks(
                    run_id, task_id, lane, status, priority, dependencies_json,
                    write_set_json, prompt_path, metadata_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    task_id,
                    lane,
                    status,
                    int(priority),
                    json_dumps(dependencies_list),
                    json_dumps(list(write_set or [])),
                    prompt_path,
                    json_dumps(metadata or {}),
                    now,
                    now,
                ),
            )
        self.publish_event(
            "task.seeded",
            run_id,
            lane=lane,
            task_id=task_id,
            payload={"status": status, "priority": priority},
        )
        return task_id

    def set_run_status(self, run_id: str, status: str) -> None:
        if status not in {"active", "paused", "draining", "completed", "aborted"}:
            raise ValueError(f"Unsupported run status: {status}")
        with self.conn:
            self.conn.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
            if status == "draining":
                self.conn.execute(
                    "UPDATE lanes SET admission_state='draining' WHERE run_id=?",
                    (run_id,),
                )
        self.publish_event(f"run.{status}", run_id)

    def _active_write_sets(self, run_id: str) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT write_set_json FROM tasks
            WHERE run_id=? AND status IN ('leased', 'running')
            """,
            (run_id,),
        ).fetchall()
        active: set[str] = set()
        for row in rows:
            active.update(json.loads(row["write_set_json"] or "[]"))
        return active

    def _pick_ready_task(self, run_id: str, lane: str) -> sqlite3.Row | None:
        active = self._active_write_sets(run_id)
        rows = self.conn.execute(
            """
            SELECT * FROM tasks
            WHERE run_id=? AND lane=? AND status IN ('ready', 'retry_ready')
            ORDER BY priority DESC, created_at ASC
            """,
            (run_id, lane),
        ).fetchall()
        for row in rows:
            write_set = set(json.loads(row["write_set_json"] or "[]"))
            if not (write_set & active):
                return row
        return None

    def _release_unblocked_tasks(self, run_id: str, now: str | None = None) -> list[str]:
        """Move blocked tasks to ready once all dependencies completed."""
        now = now or utc_now()
        rows = self.conn.execute(
            """
            SELECT task_id, dependencies_json FROM tasks
            WHERE run_id=? AND status='blocked'
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        released: list[str] = []
        for row in rows:
            dependencies = json.loads(row["dependencies_json"] or "[]")
            if not dependencies:
                ready = True
            else:
                completed_count = self.conn.execute(
                    """
                    SELECT COUNT(*) FROM tasks
                    WHERE run_id=? AND task_id IN ({}) AND status='completed'
                    """.format(",".join("?" for _ in dependencies)),
                    (run_id, *dependencies),
                ).fetchone()[0]
                ready = completed_count == len(set(dependencies))
            if ready:
                self.conn.execute(
                    "UPDATE tasks SET status='ready', updated_at=? WHERE run_id=? AND task_id=?",
                    (now, run_id, row["task_id"]),
                )
                released.append(row["task_id"])
        return released

    def claim_next_task(self, run_id: str, slot_id: str) -> Assignment | None:
        with self.conn:
            slot = self.conn.execute(
                "SELECT * FROM slots WHERE run_id=? AND slot_id=?",
                (run_id, slot_id),
            ).fetchone()
            if not slot or slot["status"] != "idle":
                return None
            run = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run or run["status"] != "active":
                return None
            lane = self.conn.execute(
                "SELECT * FROM lanes WHERE run_id=? AND lane=?",
                (run_id, slot["lane"]),
            ).fetchone()
            if not lane or lane["admission_state"] != "open":
                return None
            active_count = self.conn.execute(
                """
                SELECT COUNT(*) FROM slots
                WHERE run_id=? AND lane=? AND status IN ('leased', 'busy')
                """,
                (run_id, slot["lane"]),
            ).fetchone()[0]
            if active_count >= int(lane["max_slots"]):
                return None
            task = self._pick_ready_task(run_id, slot["lane"])
            if task is None:
                return None

            attempt_id = f"attempt-{uuid.uuid4().hex[:12]}"
            lease_id = f"lease-{uuid.uuid4().hex[:12]}"
            now = utc_now()
            expires = utc_deadline(int(slot["heartbeat_timeout_sec"]))
            self.conn.execute(
                "UPDATE tasks SET status='leased', updated_at=? WHERE run_id=? AND task_id=?",
                (now, run_id, task["task_id"]),
            )
            self.conn.execute(
                "UPDATE slots SET status='leased', current_lease_id=?, last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                (lease_id, now, run_id, slot_id),
            )
            self.conn.execute(
                """
                INSERT INTO attempts(
                    run_id, attempt_id, task_id, slot_id, lease_id, status, enqueued_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (run_id, attempt_id, task["task_id"], slot_id, lease_id, "pending", now),
            )
            self.conn.execute(
                """
                INSERT INTO leases(
                    run_id, lease_id, task_id, attempt_id, slot_id, status, issued_at, expires_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (run_id, lease_id, task["task_id"], attempt_id, slot_id, "active", now, expires),
            )
        self.publish_event(
            "task.claimed",
            run_id,
            lane=slot["lane"],
            task_id=task["task_id"],
            attempt_id=attempt_id,
            lease_id=lease_id,
            slot_id=slot_id,
        )
        return Assignment(
            task_id=task["task_id"],
            attempt_id=attempt_id,
            lease_id=lease_id,
            slot_id=slot_id,
            lane=slot["lane"],
            prompt_path=task["prompt_path"],
            metadata=json.loads(task["metadata_json"] or "{}"),
        )

    def start_attempt(self, run_id: str, attempt_id: str) -> None:
        now = utc_now()
        row = self.conn.execute(
            "SELECT task_id, slot_id, lease_id FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, attempt_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        with self.conn:
            self.conn.execute(
                "UPDATE attempts SET status='running', started_at=? WHERE run_id=? AND attempt_id=?",
                (now, run_id, attempt_id),
            )
            self.conn.execute(
                "UPDATE tasks SET status='running', updated_at=? WHERE run_id=? AND task_id=?",
                (now, run_id, row["task_id"]),
            )
            self.conn.execute(
                "UPDATE slots SET status='busy', last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                (now, run_id, row["slot_id"]),
            )
        self.publish_event(
            "attempt.started",
            run_id,
            task_id=row["task_id"],
            attempt_id=attempt_id,
            lease_id=row["lease_id"],
            slot_id=row["slot_id"],
        )

    def heartbeat_attempt(self, run_id: str, attempt_id: str, *, payload: dict[str, Any] | None = None) -> bool:
        row = self.conn.execute(
            """
            SELECT
                attempts.task_id,
                attempts.slot_id,
                attempts.lease_id,
                attempts.status AS attempt_status,
                slots.heartbeat_timeout_sec,
                leases.status AS lease_status
            FROM attempts
            JOIN slots ON slots.run_id=attempts.run_id AND slots.slot_id=attempts.slot_id
            JOIN leases ON leases.run_id=attempts.run_id AND leases.lease_id=attempts.lease_id
            WHERE attempts.run_id=? AND attempts.attempt_id=?
            """,
            (run_id, attempt_id),
        ).fetchone()
        if not row or row["attempt_status"] != "running" or row["lease_status"] != "active":
            return False
        now = utc_now()
        expires = utc_deadline(int(row["heartbeat_timeout_sec"]))
        with self.conn:
            self.conn.execute(
                "UPDATE slots SET last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                (now, run_id, row["slot_id"]),
            )
            self.conn.execute(
                "UPDATE leases SET expires_at=? WHERE run_id=? AND lease_id=? AND status='active'",
                (expires, run_id, row["lease_id"]),
            )
        self.publish_event(
            "lease.heartbeat",
            run_id,
            task_id=row["task_id"],
            attempt_id=attempt_id,
            lease_id=row["lease_id"],
            slot_id=row["slot_id"],
            payload={"expires_at": expires, **(payload or {})},
        )
        return True

    def expire_stale_leases(self, run_id: str, *, lane: str | None = None) -> list[dict[str, Any]]:
        now = utc_now()
        params: list[Any] = [run_id, now]
        lane_filter = ""
        if lane is not None:
            lane_filter = " AND tasks.lane=?"
            params.append(lane)
        rows = self.conn.execute(
            f"""
            SELECT
                leases.lease_id,
                leases.task_id,
                leases.attempt_id,
                leases.slot_id,
                leases.expires_at,
                tasks.lane,
                tasks.status AS task_status,
                attempts.status AS attempt_status
            FROM leases
            JOIN tasks ON tasks.run_id=leases.run_id AND tasks.task_id=leases.task_id
            JOIN attempts ON attempts.run_id=leases.run_id AND attempts.attempt_id=leases.attempt_id
            WHERE leases.run_id=? AND leases.status='active' AND leases.expires_at <= ?
              AND tasks.status IN ('leased', 'running')
              AND attempts.status IN ('pending', 'running')
              {lane_filter}
            ORDER BY leases.expires_at ASC
            """,
            params,
        ).fetchall()
        expired: list[dict[str, Any]] = []
        with self.conn:
            for row in rows:
                self.conn.execute(
                    "UPDATE leases SET status='expired' WHERE run_id=? AND lease_id=?",
                    (run_id, row["lease_id"]),
                )
                self.conn.execute(
                    """
                    UPDATE attempts
                    SET status='failed', completed_at=?, failure_class=?, diagnostics=?
                    WHERE run_id=? AND attempt_id=?
                    """,
                    (
                        now,
                        "lease_expired",
                        f"Lease expired at {row['expires_at']}",
                        run_id,
                        row["attempt_id"],
                    ),
                )
                self.conn.execute(
                    "UPDATE tasks SET status='retry_ready', updated_at=? WHERE run_id=? AND task_id=?",
                    (now, run_id, row["task_id"]),
                )
                self.conn.execute(
                    "UPDATE slots SET status='idle', current_lease_id=NULL, last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                    (now, run_id, row["slot_id"]),
                )
                expired.append(dict(row))
        for row in expired:
            self.publish_event(
                "lease.expired",
                run_id,
                lane=row["lane"],
                task_id=row["task_id"],
                attempt_id=row["attempt_id"],
                lease_id=row["lease_id"],
                slot_id=row["slot_id"],
                payload={"expired_at": now, "previous_expires_at": row["expires_at"]},
            )
        return expired

    def _publish_terminal_update_ignored(
        self,
        run_id: str,
        attempt_id: str,
        row: sqlite3.Row,
        *,
        requested_status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.publish_event(
            "attempt.terminal_update_ignored",
            run_id,
            task_id=row["task_id"],
            attempt_id=attempt_id,
            lease_id=row["lease_id"],
            slot_id=row["slot_id"],
            payload={
                "requested_status": requested_status,
                "current_attempt_status": row["status"],
                **(payload or {}),
            },
        )

    def complete_attempt(self, run_id: str, attempt_id: str, result: dict[str, Any] | None = None) -> bool:
        now = utc_now()
        row = self.conn.execute(
            "SELECT task_id, slot_id, lease_id, status FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, attempt_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        if row["status"] not in {"pending", "running"}:
            result_payload = result or {}
            self._publish_terminal_update_ignored(
                run_id,
                attempt_id,
                row,
                requested_status="completed",
                payload={
                    "reported_returncode": result_payload.get("returncode")
                    if isinstance(result_payload, dict)
                    else None,
                },
            )
            return False
        with self.conn:
            self.conn.execute(
                "UPDATE attempts SET status='completed', completed_at=?, result_json=? WHERE run_id=? AND attempt_id=?",
                (now, json_dumps(result or {}), run_id, attempt_id),
            )
            self.conn.execute(
                "UPDATE tasks SET status='completed', updated_at=? WHERE run_id=? AND task_id=?",
                (now, run_id, row["task_id"]),
            )
            self.conn.execute(
                "UPDATE leases SET status='released' WHERE run_id=? AND lease_id=?",
                (run_id, row["lease_id"]),
            )
            self.conn.execute(
                "UPDATE slots SET status='idle', current_lease_id=NULL, last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                (now, run_id, row["slot_id"]),
            )
            released = self._release_unblocked_tasks(run_id, now)
        self.publish_event(
            "attempt.completed",
            run_id,
            task_id=row["task_id"],
            attempt_id=attempt_id,
            lease_id=row["lease_id"],
            slot_id=row["slot_id"],
            payload=result or {},
        )
        for task_id in released:
            self.publish_event("task.unblocked", run_id, task_id=task_id)
        return True

    def fail_attempt(
        self,
        run_id: str,
        attempt_id: str,
        *,
        failure_class: str,
        diagnostics: str = "",
        retryable: bool = True,
    ) -> bool:
        now = utc_now()
        row = self.conn.execute(
            "SELECT task_id, slot_id, lease_id, status FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, attempt_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        if row["status"] not in {"pending", "running"}:
            self._publish_terminal_update_ignored(
                run_id,
                attempt_id,
                row,
                requested_status="failed",
                payload={"failure_class": failure_class, "retryable": retryable},
            )
            return False
        next_status = "retry_ready" if retryable else "rejected"
        with self.conn:
            self.conn.execute(
                """
                UPDATE attempts
                SET status='failed', completed_at=?, failure_class=?, diagnostics=?
                WHERE run_id=? AND attempt_id=?
                """,
                (now, failure_class, diagnostics, run_id, attempt_id),
            )
            self.conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE run_id=? AND task_id=?",
                (next_status, now, run_id, row["task_id"]),
            )
            self.conn.execute(
                "UPDATE leases SET status='released' WHERE run_id=? AND lease_id=?",
                (run_id, row["lease_id"]),
            )
            self.conn.execute(
                "UPDATE slots SET status='idle', current_lease_id=NULL, last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                (now, run_id, row["slot_id"]),
            )
        self.publish_event(
            "attempt.failed",
            run_id,
            task_id=row["task_id"],
            attempt_id=attempt_id,
            lease_id=row["lease_id"],
            slot_id=row["slot_id"],
            payload={"failure_class": failure_class, "retryable": retryable},
        )
        return True

    def cancel_attempt(
        self,
        run_id: str,
        attempt_id: str,
        *,
        diagnostics: str = "Canceled by operator.",
        retryable: bool = True,
    ) -> dict[str, Any] | None:
        now = utc_now()
        row = self.conn.execute(
            """
            SELECT
                attempts.task_id,
                attempts.slot_id,
                attempts.lease_id,
                attempts.status AS attempt_status,
                tasks.lane,
                tasks.status AS task_status,
                leases.status AS lease_status
            FROM attempts
            JOIN tasks ON tasks.run_id=attempts.run_id AND tasks.task_id=attempts.task_id
            LEFT JOIN leases ON leases.run_id=attempts.run_id AND leases.lease_id=attempts.lease_id
            WHERE attempts.run_id=? AND attempts.attempt_id=?
            """,
            (run_id, attempt_id),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        if row["attempt_status"] not in {"pending", "running"}:
            return None
        next_status = "retry_ready" if retryable else "rejected"
        with self.conn:
            self.conn.execute(
                """
                UPDATE attempts
                SET status='failed', completed_at=?, failure_class=?, diagnostics=?
                WHERE run_id=? AND attempt_id=? AND status IN ('pending', 'running')
                """,
                (now, "attempt_canceled", diagnostics, run_id, attempt_id),
            )
            self.conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE run_id=? AND task_id=?",
                (next_status, now, run_id, row["task_id"]),
            )
            if row["lease_id"]:
                self.conn.execute(
                    "UPDATE leases SET status='released' WHERE run_id=? AND lease_id=?",
                    (run_id, row["lease_id"]),
                )
            if row["slot_id"]:
                self.conn.execute(
                    "UPDATE slots SET status='idle', current_lease_id=NULL, last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                    (now, run_id, row["slot_id"]),
                )
        record = dict(row)
        record.update(
            {
                "attempt_id": attempt_id,
                "failure_class": "attempt_canceled",
                "diagnostics": diagnostics,
                "retryable": retryable,
                "next_task_status": next_status,
                "canceled_at": now,
            }
        )
        self.publish_event(
            "attempt.canceled",
            run_id,
            lane=row["lane"],
            task_id=row["task_id"],
            attempt_id=attempt_id,
            lease_id=row["lease_id"],
            slot_id=row["slot_id"],
            payload={"retryable": retryable, "diagnostics": diagnostics},
        )
        return record

    def attempt_records(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        status: str | None = None,
        failure_class: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [run_id]
        filters = []
        if task_id is not None:
            filters.append("attempts.task_id=?")
            params.append(task_id)
        if status is not None:
            filters.append("attempts.status=?")
            params.append(status)
        if failure_class is not None:
            filters.append("attempts.failure_class=?")
            params.append(failure_class)
        where = ""
        if filters:
            where = " AND " + " AND ".join(filters)
        params.append(max(0, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT
                attempts.run_id,
                attempts.attempt_id,
                attempts.task_id,
                tasks.lane,
                attempts.slot_id,
                attempts.lease_id,
                attempts.status,
                attempts.enqueued_at,
                attempts.started_at,
                attempts.completed_at,
                attempts.failure_class,
                attempts.diagnostics,
                attempts.result_json
            FROM attempts
            JOIN tasks ON tasks.run_id=attempts.run_id AND tasks.task_id=attempts.task_id
            WHERE attempts.run_id=?{where}
            ORDER BY attempts.enqueued_at DESC, attempts.rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        attempts = []
        for row in rows:
            record = dict(row)
            record["result"] = json.loads(record.pop("result_json") or "{}")
            attempts.append(record)
        return attempts

    def task_records(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        lane: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [run_id]
        filters = []
        if task_id is not None:
            filters.append("tasks.task_id=?")
            params.append(task_id)
        if lane is not None:
            filters.append("tasks.lane=?")
            params.append(lane)
        if status is not None:
            filters.append("tasks.status=?")
            params.append(status)
        where = ""
        if filters:
            where = " AND " + " AND ".join(filters)
        params.append(max(0, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT
                tasks.run_id,
                tasks.task_id,
                tasks.lane,
                tasks.status,
                tasks.priority,
                tasks.dependencies_json,
                tasks.write_set_json,
                tasks.prompt_path,
                tasks.metadata_json,
                tasks.created_at,
                tasks.updated_at,
                (
                    SELECT COUNT(*)
                    FROM attempts
                    WHERE attempts.run_id=tasks.run_id AND attempts.task_id=tasks.task_id
                ) AS attempt_count,
                latest_attempt.attempt_id AS latest_attempt_id,
                latest_attempt.status AS latest_attempt_status,
                latest_attempt.failure_class AS latest_failure_class
            FROM tasks
            LEFT JOIN attempts AS latest_attempt
              ON latest_attempt.run_id=tasks.run_id
             AND latest_attempt.task_id=tasks.task_id
             AND NOT EXISTS (
                SELECT 1
                FROM attempts AS newer_attempt
                WHERE newer_attempt.run_id=latest_attempt.run_id
                  AND newer_attempt.task_id=latest_attempt.task_id
                  AND (
                    newer_attempt.enqueued_at > latest_attempt.enqueued_at
                    OR (
                      newer_attempt.enqueued_at = latest_attempt.enqueued_at
                      AND newer_attempt.rowid > latest_attempt.rowid
                    )
                  )
             )
            WHERE tasks.run_id=?{where}
            ORDER BY tasks.priority DESC, tasks.created_at ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        tasks = []
        for row in rows:
            record = dict(row)
            record["dependencies"] = json.loads(record.pop("dependencies_json") or "[]")
            record["write_set"] = json.loads(record.pop("write_set_json") or "[]")
            record["metadata"] = json.loads(record.pop("metadata_json") or "{}")
            tasks.append(record)
        return tasks

    def latest_completed_attempts(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        include_verified: bool = False,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [run_id]
        task_filter = ""
        if task_id is not None:
            task_filter = " AND tasks.task_id=?"
            params.append(task_id)
        verified_filter = ""
        if not include_verified:
            verified_filter = """
              AND NOT EXISTS (
                SELECT 1 FROM verifications
                WHERE verifications.run_id=attempts.run_id
                  AND verifications.attempt_id=attempts.attempt_id
              )
            """
        rows = self.conn.execute(
            f"""
            SELECT
                tasks.task_id,
                tasks.lane,
                tasks.write_set_json,
                tasks.metadata_json,
                attempts.attempt_id,
                attempts.completed_at,
                attempts.result_json
            FROM tasks
            JOIN attempts ON attempts.run_id=tasks.run_id AND attempts.task_id=tasks.task_id
            WHERE tasks.run_id=? AND tasks.status='completed' AND attempts.status='completed'
              {task_filter}
              {verified_filter}
              AND attempts.completed_at = (
                SELECT MAX(inner_attempts.completed_at)
                FROM attempts AS inner_attempts
                WHERE inner_attempts.run_id=attempts.run_id
                  AND inner_attempts.task_id=attempts.task_id
                  AND inner_attempts.status='completed'
              )
            ORDER BY tasks.task_id ASC
            """,
            params,
        ).fetchall()
        attempts = []
        for row in rows:
            record = dict(row)
            record["write_set"] = json.loads(record.pop("write_set_json") or "[]")
            record["metadata"] = json.loads(record.pop("metadata_json") or "{}")
            record["result"] = json.loads(record.pop("result_json") or "{}")
            attempts.append(record)
        return attempts

    def record_verification(
        self,
        run_id: str,
        *,
        task_id: str,
        attempt_id: str,
        decision: str,
        verifier: str,
        evidence: dict[str, Any] | None = None,
        diagnostics: str = "",
    ) -> str:
        if decision not in {"accepted", "rejected", "held"}:
            raise ValueError(f"Unsupported verification decision: {decision}")
        verification_id = f"verification-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO verifications(
                    run_id, verification_id, task_id, attempt_id, decision,
                    verifier, verified_at, evidence_json, diagnostics
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    verification_id,
                    task_id,
                    attempt_id,
                    decision,
                    verifier,
                    now,
                    json_dumps(evidence or {}),
                    diagnostics,
                ),
            )
        self.publish_event(
            f"verification.{decision}",
            run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            payload={
                "verification_id": verification_id,
                "verifier": verifier,
                "diagnostics": diagnostics,
                "evidence": evidence or {},
            },
        )
        return verification_id

    def verification_records(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        decision: str | None = None,
        limit: int = 20,
        latest: bool = False,
        current_attempts: bool = False,
    ) -> list[dict[str, Any]]:
        if decision is not None and decision not in {"accepted", "rejected", "held"}:
            raise ValueError(f"Unsupported verification decision: {decision}")
        params: list[Any] = [run_id]
        filters = []
        if task_id is not None:
            filters.append("task_id=?")
            params.append(task_id)
        if decision is not None:
            filters.append("decision=?")
            params.append(decision)
        where = ""
        if filters:
            where = " AND " + " AND ".join(filters)
        latest_filter = ""
        if latest:
            latest_filter = """
              AND NOT EXISTS (
                SELECT 1 FROM verifications AS newer
                WHERE newer.run_id=verifications.run_id
                  AND newer.attempt_id=verifications.attempt_id
                  AND (
                    newer.verified_at > verifications.verified_at
                    OR (
                      newer.verified_at = verifications.verified_at
                      AND newer.rowid > verifications.rowid
                    )
                  )
              )
            """
        current_attempt_filter = ""
        if current_attempts:
            current_attempt_filter = """
              AND EXISTS (
                SELECT 1
                FROM attempts AS current_attempt
                JOIN tasks ON tasks.run_id=current_attempt.run_id
                  AND tasks.task_id=current_attempt.task_id
                WHERE current_attempt.run_id=verifications.run_id
                  AND current_attempt.attempt_id=verifications.attempt_id
                  AND current_attempt.status='completed'
                  AND tasks.status='completed'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM attempts AS newer_attempt
                    WHERE newer_attempt.run_id=current_attempt.run_id
                      AND newer_attempt.task_id=current_attempt.task_id
                      AND newer_attempt.status='completed'
                      AND (
                        newer_attempt.completed_at > current_attempt.completed_at
                        OR (
                          newer_attempt.completed_at = current_attempt.completed_at
                          AND newer_attempt.rowid > current_attempt.rowid
                        )
                      )
                  )
              )
            """
        params.append(max(0, int(limit)))
        rows = self.conn.execute(
            f"""
            SELECT
                run_id,
                verification_id,
                task_id,
                attempt_id,
                decision,
                verifier,
                verified_at,
                evidence_json,
                diagnostics
            FROM verifications
            WHERE run_id=?{where}{latest_filter}{current_attempt_filter}
            ORDER BY verified_at DESC, rowid DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["evidence"] = json.loads(record.pop("evidence_json") or "{}")
            records.append(record)
        return records

    def pending_verification_count(self, run_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM tasks
            JOIN attempts ON attempts.run_id=tasks.run_id AND attempts.task_id=tasks.task_id
            WHERE tasks.run_id=? AND tasks.status='completed' AND attempts.status='completed'
              AND NOT EXISTS (
                SELECT 1 FROM verifications
                WHERE verifications.run_id=attempts.run_id
                  AND verifications.attempt_id=attempts.attempt_id
              )
              AND attempts.completed_at = (
                SELECT MAX(inner_attempts.completed_at)
                FROM attempts AS inner_attempts
                WHERE inner_attempts.run_id=attempts.run_id
                  AND inner_attempts.task_id=attempts.task_id
                  AND inner_attempts.status='completed'
              )
            """,
            (run_id,),
        ).fetchone()
        return int(row["count"])

    def current_verification_counts(self, run_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT decision, COUNT(*) AS count
            FROM verifications
            WHERE run_id=?
              AND EXISTS (
                SELECT 1
                FROM attempts AS current_attempt
                JOIN tasks ON tasks.run_id=current_attempt.run_id
                  AND tasks.task_id=current_attempt.task_id
                WHERE current_attempt.run_id=verifications.run_id
                  AND current_attempt.attempt_id=verifications.attempt_id
                  AND current_attempt.status='completed'
                  AND tasks.status='completed'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM attempts AS newer_attempt
                    WHERE newer_attempt.run_id=current_attempt.run_id
                      AND newer_attempt.task_id=current_attempt.task_id
                      AND newer_attempt.status='completed'
                      AND (
                        newer_attempt.completed_at > current_attempt.completed_at
                        OR (
                          newer_attempt.completed_at = current_attempt.completed_at
                          AND newer_attempt.rowid > current_attempt.rowid
                        )
                      )
                  )
              )
              AND NOT EXISTS (
                SELECT 1 FROM verifications AS newer
                WHERE newer.run_id=verifications.run_id
                  AND newer.attempt_id=verifications.attempt_id
                  AND (
                    newer.verified_at > verifications.verified_at
                    OR (
                      newer.verified_at = verifications.verified_at
                      AND newer.rowid > verifications.rowid
                    )
                  )
              )
            GROUP BY decision
            """,
            (run_id,),
        ).fetchall()
        return {row["decision"]: row["count"] for row in rows}

    def status_summary(self, run_id: str) -> dict[str, Any]:
        run = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise KeyError(f"Run not found: {run_id}")
        task_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM tasks WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
        slot_rows = self.conn.execute(
            "SELECT lane, status, COUNT(*) AS count FROM slots WHERE run_id=? GROUP BY lane, status",
            (run_id,),
        ).fetchall()
        lane_rows = self.conn.execute(
            "SELECT lane, max_slots, admission_state, backpressure FROM lanes WHERE run_id=? ORDER BY lane",
            (run_id,),
        ).fetchall()
        lease_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM leases WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
        active_lease_rows = self.conn.execute(
            """
            SELECT
                leases.lease_id,
                leases.task_id,
                leases.attempt_id,
                leases.slot_id,
                leases.issued_at,
                leases.expires_at,
                tasks.lane,
                slots.status AS slot_status,
                slots.last_heartbeat_at
            FROM leases
            JOIN tasks ON tasks.run_id=leases.run_id AND tasks.task_id=leases.task_id
            JOIN slots ON slots.run_id=leases.run_id AND slots.slot_id=leases.slot_id
            WHERE leases.run_id=? AND leases.status='active'
            ORDER BY leases.expires_at ASC
            """,
            (run_id,),
        ).fetchall()
        verification_rows = self.conn.execute(
            "SELECT decision, COUNT(*) AS count FROM verifications WHERE run_id=? GROUP BY decision",
            (run_id,),
        ).fetchall()
        now = datetime.now(timezone.utc)
        active_leases = []
        for row in active_lease_rows:
            lease = dict(row)
            lease["age_seconds"] = elapsed_seconds(now, lease.get("issued_at"))
            lease["heartbeat_age_seconds"] = elapsed_seconds(now, lease.get("last_heartbeat_at"))
            lease["expires_in_seconds"] = seconds_until(now, lease.get("expires_at"))
            active_leases.append(lease)
        return {
            "run_id": run_id,
            "status": run["status"],
            "tasks": {row["status"]: row["count"] for row in task_rows},
            "leases": {row["status"]: row["count"] for row in lease_rows},
            "active_leases": active_leases,
            "verification_pending": self.pending_verification_count(run_id),
            "verification_current": self.current_verification_counts(run_id),
            "verifications": {row["decision"]: row["count"] for row in verification_rows},
            "slots": [
                {"lane": row["lane"], "status": row["status"], "count": row["count"]}
                for row in slot_rows
            ],
            "lanes": [dict(row) for row in lane_rows],
            "run_dir": str(self.run_dir),
        }

    def recent_events(
        self,
        run_id: str,
        *,
        limit: int = 20,
        message_type: str | None = None,
        controller_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [run_id]
        filters = []
        if message_type:
            filters.append("message_type=?")
            params.append(message_type)
        if controller_id:
            filters.append("controller_id=?")
            params.append(controller_id)
        filter_sql = "".join(
            f"\n              AND {filter_clause}" for filter_clause in filters
        )
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT
                sequence_no,
                message_id,
                run_id,
                message_type,
                lane,
                task_id,
                attempt_id,
                lease_id,
                slot_id,
                controller_id,
                timestamp,
                payload_json
            FROM events
            WHERE run_id=?
              {filter_sql}
            ORDER BY sequence_no DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        events = []
        for row in reversed(rows):
            record = dict(row)
            record["payload"] = json.loads(record.pop("payload_json") or "{}")
            events.append(record)
        return events


def run_dir_for(run_root: str | Path, run_id: str) -> Path:
    return Path(run_root).expanduser().resolve() / run_id


def runtime_for(run_root: str | Path, run_id: str, *, controller_id: str | None = None) -> SMIRuntime:
    rd = run_dir_for(run_root, run_id)
    return SMIRuntime(rd / "run.db", rd, controller_id=controller_id)

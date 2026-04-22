"""Account-scoped resource gate for coordinating multiple SMI project runs."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .smi_core import utc_now


DEFAULT_ACCOUNT_RESOURCES: dict[str, dict[str, Any]] = {
    "remote_transfer": {
        "max_slots": 1,
        "description": "Shared account transfer bandwidth for rsync/scp/Globus staging and harvest.",
    },
    "remote_submit": {
        "max_slots": 1,
        "description": "Shared account submit/session-touch budget for scheduler-facing commands.",
    },
    "remote_monitor": {
        "max_slots": 2,
        "description": "Shared account budget for lightweight squeue/sacct and output polling.",
    },
    "remote_cluster": {
        "max_slots": 1,
        "description": "Compatibility resource for older all-in-one remote cluster tasks.",
    },
}


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS resources (
    account_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    max_slots INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, resource)
);

CREATE TABLE IF NOT EXISTS permits (
    account_id TEXT NOT NULL,
    permit_id TEXT PRIMARY KEY,
    resource TEXT NOT NULL,
    holder TEXT NOT NULL,
    run_id TEXT,
    task_id TEXT,
    slot_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_permits_active
ON permits(account_id, resource, status, expires_at);
"""


@dataclass(frozen=True)
class AccountPermit:
    account_id: str
    permit_id: str
    resource: str
    holder: str
    run_id: str | None
    task_id: str | None
    slot_id: str | None
    issued_at: str
    expires_at: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "account_id": self.account_id,
            "permit_id": self.permit_id,
            "resource": self.resource,
            "holder": self.holder,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "slot_id": self.slot_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


def safe_account_name(account_id: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in account_id.strip())
    return clean or "default"


def account_gate_path(root: str | Path, account_id: str) -> Path:
    name = safe_account_name(account_id)
    directory = name if name.startswith("SMI_account") else f"SMI_account_{name}"
    return Path(root).expanduser().resolve() / directory / "account_gate.sqlite"


class AccountGate:
    """Small SQLite semaphore shared by independent SMI project runs."""

    def __init__(self, account_id: str, db_path: str | Path) -> None:
        self.account_id = account_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        existing = self.conn.execute(
            "SELECT COUNT(*) FROM resources WHERE account_id=?",
            (self.account_id,),
        ).fetchone()[0]
        if existing == 0:
            self.configure_resources(DEFAULT_ACCOUNT_RESOURCES)

    def close(self) -> None:
        self.conn.close()

    def configure_resources(self, resources: dict[str, Any], *, replace: bool = False) -> None:
        now = utc_now()
        with self.conn:
            if replace:
                keep = list(resources)
                if keep:
                    placeholders = ",".join("?" for _ in keep)
                    self.conn.execute(
                        f"""
                        DELETE FROM resources
                        WHERE account_id=? AND resource NOT IN ({placeholders})
                        """,
                        (self.account_id, *keep),
                    )
                else:
                    self.conn.execute("DELETE FROM resources WHERE account_id=?", (self.account_id,))
            for resource, config in resources.items():
                if not isinstance(config, dict):
                    config = {"max_slots": int(config)}
                self.conn.execute(
                    """
                    INSERT INTO resources(account_id, resource, max_slots, description, updated_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(account_id, resource) DO UPDATE SET
                        max_slots=excluded.max_slots,
                        description=excluded.description,
                        updated_at=excluded.updated_at
                    """,
                    (
                        self.account_id,
                        resource,
                        int(config.get("max_slots", 1)),
                        str(config.get("description", "")),
                        now,
                    ),
                )

    def acquire(
        self,
        resource: str,
        *,
        holder: str,
        run_id: str | None = None,
        task_id: str | None = None,
        slot_id: str | None = None,
        ttl_sec: int = 14_400,
    ) -> AccountPermit | None:
        now = utc_now()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        permit_id = f"permit-{uuid.uuid4().hex[:12]}"

        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._reap_expired_locked(now)
            resource_row = self.conn.execute(
                "SELECT max_slots FROM resources WHERE account_id=? AND resource=?",
                (self.account_id, resource),
            ).fetchone()
            if resource_row is None:
                self.conn.execute(
                    """
                    INSERT INTO resources(account_id, resource, max_slots, description, updated_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (self.account_id, resource, 1, "Implicit account resource.", now),
                )
                max_slots = 1
            else:
                max_slots = int(resource_row["max_slots"])

            active_count = self.conn.execute(
                """
                SELECT COUNT(*) FROM permits
                WHERE account_id=? AND resource=? AND status='active'
                """,
                (self.account_id, resource),
            ).fetchone()[0]
            if active_count >= max_slots:
                self.conn.execute("COMMIT")
                return None

            self.conn.execute(
                """
                INSERT INTO permits(
                    account_id, permit_id, resource, holder, run_id, task_id,
                    slot_id, status, issued_at, expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.account_id,
                    permit_id,
                    resource,
                    holder,
                    run_id,
                    task_id,
                    slot_id,
                    "active",
                    now,
                    expires_at,
                ),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        return AccountPermit(
            account_id=self.account_id,
            permit_id=permit_id,
            resource=resource,
            holder=holder,
            run_id=run_id,
            task_id=task_id,
            slot_id=slot_id,
            issued_at=now,
            expires_at=expires_at,
        )

    def release(self, permit_id: str) -> bool:
        now = utc_now()
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE permits
                SET status='released', expires_at=?
                WHERE account_id=? AND permit_id=? AND status='active'
                """,
                (now, self.account_id, permit_id),
            )
        return cursor.rowcount > 0

    def renew(self, permit_id: str, *, ttl_sec: int = 14_400) -> bool:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_sec)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE permits
                SET expires_at=?
                WHERE account_id=? AND permit_id=? AND status='active'
                """,
                (expires_at, self.account_id, permit_id),
            )
        return cursor.rowcount > 0

    def reap_expired(self) -> int:
        now = utc_now()
        with self.conn:
            return self._reap_expired_locked(now)

    def _reap_expired_locked(self, now: str) -> int:
        cursor = self.conn.execute(
            """
            UPDATE permits
            SET status='expired'
            WHERE account_id=? AND status='active' AND expires_at <= ?
            """,
            (self.account_id, now),
        )
        return cursor.rowcount

    def status_summary(self) -> dict[str, Any]:
        self.reap_expired()
        resource_rows = self.conn.execute(
            """
            SELECT resource, max_slots, description
            FROM resources
            WHERE account_id=?
            ORDER BY resource
            """,
            (self.account_id,),
        ).fetchall()
        count_rows = self.conn.execute(
            """
            SELECT resource, status, COUNT(*) AS count
            FROM permits
            WHERE account_id=?
            GROUP BY resource, status
            """,
            (self.account_id,),
        ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in count_rows:
            counts.setdefault(row["resource"], {})[row["status"]] = row["count"]
        active_rows = self.conn.execute(
            """
            SELECT permit_id, resource, holder, run_id, task_id, slot_id, issued_at, expires_at
            FROM permits
            WHERE account_id=? AND status='active'
            ORDER BY resource, issued_at
            """,
            (self.account_id,),
        ).fetchall()
        return {
            "account_id": self.account_id,
            "db_path": str(self.db_path),
            "resources": [
                {
                    "resource": row["resource"],
                    "max_slots": row["max_slots"],
                    "description": row["description"],
                    "permits": counts.get(row["resource"], {}),
                }
                for row in resource_rows
            ],
            "active_permits": [dict(row) for row in active_rows],
        }


def load_resource_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)

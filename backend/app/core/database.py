from __future__ import annotations

from contextlib import contextmanager
import json
import os
import sqlite3
from datetime import datetime, timezone

from backend.app.core.config import settings
from backend.app.models.schemas import ExamRubric, SubmissionGradingRecord


class EduMarkDatabase:
    """SQLite persistence layer (WAL mode) cho ExamRubric, SubmissionGradingRecord và BatchJob."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.DB_PATH
        if self.db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self.db_path))
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS exams (
                    exam_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    exam_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_submissions_exam_id ON submissions(exam_id);
                CREATE INDEX IF NOT EXISTS idx_submissions_created_at ON submissions(created_at DESC);
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    job_id TEXT PRIMARY KEY,
                    exam_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gradebooks (
                    gradebook_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    exam_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_accounts (
                    account_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    auth_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    account_email TEXT DEFAULT '',
                    base_url TEXT DEFAULT '',
                    vision_model TEXT DEFAULT '',
                    reasoning_model TEXT DEFAULT '',
                    secret_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'connected',
                    last_tested_at TEXT DEFAULT '',
                    last_error TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_accounts_active ON provider_accounts(is_active DESC, updated_at DESC);
                CREATE TABLE IF NOT EXISTS provider_auth_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT DEFAULT '',
                    provider TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    client_ip TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_provider_auth_logs_created ON provider_auth_logs(created_at DESC, log_id DESC);
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    vision_model TEXT DEFAULT '',
                    reasoning_model TEXT DEFAULT '',
                    extra_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                """
            )

    def ping(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    # --- Exams ---
    def save_exam(self, rubric: ExamRubric) -> ExamRubric:
        now = datetime.now(timezone.utc).isoformat()
        payload = rubric.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exams (exam_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(exam_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (rubric.exam_id, payload, now),
            )
        return rubric

    def get_exam(self, exam_id: str) -> ExamRubric | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
        if not row:
            return None
        return ExamRubric.model_validate_json(row["payload_json"])

    def list_exams(self) -> list[ExamRubric]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM exams ORDER BY updated_at ASC").fetchall()
        return [ExamRubric.model_validate_json(r["payload_json"]) for r in rows]

    def delete_exam(self, exam_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM exams WHERE exam_id = ?", (exam_id,))
        return cur.rowcount > 0

    # --- Submissions ---
    def save_submission(self, record: SubmissionGradingRecord) -> SubmissionGradingRecord:
        created = record.created_at or datetime.now(timezone.utc).isoformat()
        if not record.created_at:
            record.created_at = created
        payload = record.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO submissions (submission_id, exam_id, status, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(submission_id) DO UPDATE SET
                    exam_id = excluded.exam_id,
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (record.submission_id, record.exam_id, record.status.value, created, payload),
            )
        return record

    def get_submission(self, submission_id: str) -> SubmissionGradingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
        if not row:
            return None
        return SubmissionGradingRecord.model_validate_json(row["payload_json"])

    def list_submissions(
        self,
        exam_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SubmissionGradingRecord]:
        clauses: list[str] = []
        params: list = []
        if exam_id:
            clauses.append("exam_id = ?")
            params.append(exam_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload_json FROM submissions {where_sql} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [SubmissionGradingRecord.model_validate_json(r["payload_json"]) for r in rows]

    def list_submissions_by_prefix(self, prefix: str, limit: int = 200) -> list[SubmissionGradingRecord]:
        """Lấy toàn bộ bài nộp thuộc một lô (vd `class_03b17874_hs%`) theo thứ tự submission_id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM submissions WHERE submission_id LIKE ? ORDER BY submission_id ASC LIMIT ?",
                (f"{prefix}%", max(1, min(limit, 500))),
            ).fetchall()
        return [SubmissionGradingRecord.model_validate_json(r["payload_json"]) for r in rows]

    # --- Batch Jobs ---
    def save_batch_job(self, job_dict: dict) -> None:
        payload = json.dumps(job_dict, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO batch_jobs (job_id, exam_id, status, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (
                    job_dict["job_id"],
                    job_dict["exam_id"],
                    job_dict["status"],
                    job_dict.get("created_at", datetime.now(timezone.utc).isoformat()),
                    payload,
                ),
            )

    def get_batch_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return json.loads(row["payload_json"])

    # --- Gradebooks ---
    def save_gradebook(self, meta: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        meta["updated_at"] = now
        config_json = json.dumps(meta, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gradebooks (gradebook_id, name, exam_id, file_path, config_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gradebook_id) DO UPDATE SET
                    name = excluded.name,
                    exam_id = excluded.exam_id,
                    file_path = excluded.file_path,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (
                    meta["gradebook_id"],
                    meta.get("name", "Bảng điểm"),
                    meta.get("exam_id", "AUTO"),
                    meta.get("file_path", ""),
                    config_json,
                    now,
                ),
            )
        return meta

    def get_gradebook(self, gradebook_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT config_json FROM gradebooks WHERE gradebook_id = ?", (gradebook_id,)).fetchone()
        if not row:
            return None
        return json.loads(row["config_json"])

    def list_gradebooks(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT config_json FROM gradebooks ORDER BY updated_at DESC").fetchall()
        return [json.loads(r["config_json"]) for r in rows]

    def delete_gradebook(self, gradebook_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM gradebooks WHERE gradebook_id = ?", (gradebook_id,))
        return cur.rowcount > 0

    # --- Provider Accounts, Auth Logs & OAuth States (v0.8.0) ---
    @staticmethod
    def _row_to_provider_account(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["is_active"] = bool(d.get("is_active", 0))
        return d

    def save_provider_account(self, account: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        created_at = account.get("created_at") or now
        updated_at = now
        secret_val = account.get("secret_json")
        if not isinstance(secret_val, str):
            secret_val = json.dumps(account.get("secret") or {}, ensure_ascii=False)
        is_active_int = 1 if account.get("is_active") else 0
        with self._connect() as conn:
            if is_active_int:
                conn.execute("UPDATE provider_accounts SET is_active = 0")
            conn.execute(
                """
                INSERT INTO provider_accounts (
                    account_id, provider, auth_type, label, account_email,
                    base_url, vision_model, reasoning_model, secret_json,
                    is_active, status, last_tested_at, last_error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    provider = excluded.provider,
                    auth_type = excluded.auth_type,
                    label = excluded.label,
                    account_email = excluded.account_email,
                    base_url = excluded.base_url,
                    vision_model = excluded.vision_model,
                    reasoning_model = excluded.reasoning_model,
                    secret_json = excluded.secret_json,
                    is_active = excluded.is_active,
                    status = excluded.status,
                    last_tested_at = excluded.last_tested_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    account["account_id"],
                    account["provider"],
                    account["auth_type"],
                    account.get("label", account["provider"]),
                    account.get("account_email", ""),
                    account.get("base_url", ""),
                    account.get("vision_model", ""),
                    account.get("reasoning_model", ""),
                    secret_val,
                    is_active_int,
                    account.get("status", "connected"),
                    account.get("last_tested_at", ""),
                    account.get("last_error", ""),
                    created_at,
                    updated_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM provider_accounts WHERE account_id = ?",
                (account["account_id"],),
            ).fetchone()
        return self._row_to_provider_account(row)

    def get_provider_account(self, account_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM provider_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return self._row_to_provider_account(row) if row else None

    def list_provider_accounts(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provider_accounts ORDER BY is_active DESC, updated_at DESC"
            ).fetchall()
        return [self._row_to_provider_account(r) for r in rows]

    def get_active_provider_account(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM provider_accounts WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return self._row_to_provider_account(row) if row else None

    def set_active_provider_account(self, account_id: str) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT account_id FROM provider_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE provider_accounts SET is_active = 0")
            conn.execute(
                "UPDATE provider_accounts SET is_active = 1, updated_at = ? WHERE account_id = ?",
                (now, account_id),
            )
            updated = conn.execute(
                "SELECT * FROM provider_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return self._row_to_provider_account(updated) if updated else None

    def deactivate_all_provider_accounts(self) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE provider_accounts SET is_active = 0")

    def delete_provider_account(self, account_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM provider_accounts WHERE account_id = ?", (account_id,))
        return cur.rowcount > 0

    def log_provider_auth_event(
        self,
        provider: str,
        event_type: str,
        status: str,
        detail: str,
        account_id: str = "",
        client_ip: str = "",
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO provider_auth_logs (
                    account_id, provider, event_type, status, detail, client_ip, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id or "", provider, event_type, status, detail, client_ip or "", now),
            )
            row = conn.execute(
                "SELECT * FROM provider_auth_logs WHERE log_id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def list_provider_auth_logs(self, limit: int = 50, provider: str | None = None) -> list[dict]:
        lim = max(1, min(limit, 500))
        with self._connect() as conn:
            if provider:
                rows = conn.execute(
                    "SELECT * FROM provider_auth_logs WHERE provider = ? ORDER BY log_id DESC LIMIT ?",
                    (provider, lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM provider_auth_logs ORDER BY log_id DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [dict(r) for r in rows]

    def save_oauth_state(self, state_row: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        extra_json = state_row.get("extra_json")
        if not isinstance(extra_json, str):
            extra_json = json.dumps(state_row.get("extra") or {}, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
            conn.execute(
                """
                INSERT INTO oauth_states (
                    state, provider, code_verifier, redirect_uri, label,
                    vision_model, reasoning_model, extra_json, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(state) DO UPDATE SET
                    provider = excluded.provider,
                    code_verifier = excluded.code_verifier,
                    redirect_uri = excluded.redirect_uri,
                    label = excluded.label,
                    vision_model = excluded.vision_model,
                    reasoning_model = excluded.reasoning_model,
                    extra_json = excluded.extra_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    state_row["state"],
                    state_row["provider"],
                    state_row["code_verifier"],
                    state_row["redirect_uri"],
                    state_row.get("label", ""),
                    state_row.get("vision_model", ""),
                    state_row.get("reasoning_model", ""),
                    extra_json,
                    state_row.get("created_at") or now,
                    state_row["expires_at"],
                ),
            )

    def pop_oauth_state(self, state: str) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
            row = conn.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        return dict(row)


db = EduMarkDatabase()

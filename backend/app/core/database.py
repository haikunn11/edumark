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
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
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


db = EduMarkDatabase()

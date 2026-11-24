"""
jobs.py — Job store: in-memory (default) yoki Redis (REDIS_URL bo'lsa).

Bir nechta worker / deploy uchun Redis tavsiya etiladi.
Lokal ishlash uchun hech narsa qo'shimcha o'rnatish shart emas.
"""

from __future__ import annotations

def _clean_err(msg: str) -> str:
    """ANSI va ortiqcha bo'shliqlarni olib tashlash."""
    import re
    s = re.sub(r"\x1b\[[0-9;]*[mK]", "", str(msg or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:800]



import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

REDIS_URL = os.getenv("REDIS_URL", "").strip()
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", str(2 * 3600)))  # 2 soat


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


PIPELINE_STEPS = [
    "download",       # URL bo'lsa
    "extract_audio",
    "transcribe",
    "translate",
    "tts_dub",
    "mux",
    "subtitles",
]


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    step: str = ""
    step_index: int = 0
    message: str = "Navbatda..."
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    output_path: Optional[str] = None
    job_dir: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status.value if isinstance(self.status, JobStatus) else self.status,
            "progress": self.progress,
            "step": self.step,
            "step_index": self.step_index,
            "total_steps": len(PIPELINE_STEPS),
            "message": self.message,
            "error": self.error,
            "meta": self.meta,
            "download_ready": (
                (self.status == JobStatus.DONE or self.status == "done")
                and bool(self.output_path)
            ),
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, JobStatus) else self.status
        return d

    @staticmethod
    def from_dict(data: dict) -> "Job":
        st = data.get("status", "queued")
        try:
            status = JobStatus(st)
        except Exception:
            status = JobStatus.QUEUED
        return Job(
            id=data["id"],
            status=status,
            progress=int(data.get("progress", 0)),
            step=data.get("step") or "",
            step_index=int(data.get("step_index", 0)),
            message=data.get("message") or "",
            error=data.get("error"),
            created_at=float(data.get("created_at") or time.time()),
            finished_at=data.get("finished_at"),
            output_path=data.get("output_path"),
            job_dir=data.get("job_dir"),
            meta=data.get("meta") or {},
        )


class JobStore:
    """Abstract store interface."""

    def save(self, job: Job) -> None:
        raise NotImplementedError

    def get(self, job_id: str) -> Optional[Job]:
        raise NotImplementedError

    def delete(self, job_id: str) -> None:
        raise NotImplementedError

    def purge_old(self) -> int:
        raise NotImplementedError

    def count_active(self) -> int:
        raise NotImplementedError


class MemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def purge_old(self) -> int:
        cutoff = time.time() - JOB_TTL_SECONDS
        removed = 0
        with self._lock:
            to_delete = [jid for jid, j in self._jobs.items() if j.created_at < cutoff]
            for jid in to_delete:
                self._jobs.pop(jid, None)
                removed += 1
        return removed

    def count_active(self) -> int:
        with self._lock:
            return sum(
                1
                for j in self._jobs.values()
                if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            )


class RedisJobStore(JobStore):
    def __init__(self, url: str) -> None:
        import redis  # type: ignore

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = "nisme:job:"

    def _key(self, job_id: str) -> str:
        return f"{self._prefix}{job_id}"

    def save(self, job: Job) -> None:
        key = self._key(job.id)
        self._r.set(key, json.dumps(job.to_dict()), ex=JOB_TTL_SECONDS)

    def get(self, job_id: str) -> Optional[Job]:
        raw = self._r.get(self._key(job_id))
        if not raw:
            return None
        return Job.from_dict(json.loads(raw))

    def delete(self, job_id: str) -> None:
        self._r.delete(self._key(job_id))

    def purge_old(self) -> int:
        # TTL Redis da avtomatik — qo'lda tozalash shart emas
        return 0

    def count_active(self) -> int:
        count = 0
        for key in self._r.scan_iter(match=f"{self._prefix}*", count=100):
            raw = self._r.get(key)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                if data.get("status") in ("queued", "running"):
                    count += 1
            except Exception:
                continue
        return count


def create_job_store() -> JobStore:
    if REDIS_URL:
        try:
            store = RedisJobStore(REDIS_URL)
            # connectivity check
            store._r.ping()
            print(f"✅ Job store: Redis ({REDIS_URL.split('@')[-1] if '@' in REDIS_URL else 'configured'})")
            return store
        except Exception as e:
            print(f"⚠️  Redis ulanmadi ({e}), in-memory store ishlatiladi")
    print("ℹ️  Job store: in-memory (bir worker uchun OK; multi-worker uchun REDIS_URL qo'ying)")
    return MemoryJobStore()

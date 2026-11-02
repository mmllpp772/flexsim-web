"""
直連模式（需要開 port）
-----------------------
在桌機上啟動後，同公司網路的人用瀏覽器打開 http://<你的電腦名稱>:8000
就能輸入參數、執行 FlexSim 模擬、看指標。

    瀏覽器 (index.html)  --HTTP-->  這個 FastAPI 服務  --FlexSimPy-->  FlexSim

不想開 port 的話，改用 onedrive_worker.py（OneDrive 中轉模式）。
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from runner import (BASE_DIR, FlexSimRunner, load_config, log, make_runner, now_iso,
                    public_config, setup_logging, validate_params)

setup_logging("server.log")
CONFIG = load_config()
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)


class RunRequest(BaseModel):
    parameters: dict[str, float]
    run_length: float | None = None
    label: str | None = None
    submitted_by: str | None = None


jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
job_queue: "queue.Queue[str]" = queue.Queue()
history: list[str] = []  # 已完成的 job id，新的在前


def worker() -> None:
    runner = make_runner(CONFIG)
    while True:
        job_id = job_queue.get()
        with jobs_lock:
            job = jobs[job_id]
            job["status"] = "running"
            job["started_at"] = now_iso()
        log.info("Running job %s %s", job_id, job["parameters"])
        try:
            result = runner.run(job["parameters"], job["run_length"])
            with jobs_lock:
                job.update(status="done", finished_at=now_iso(), **result)
        except Exception as e:
            log.exception("Job %s failed", job_id)
            with jobs_lock:
                job.update(status="error", finished_at=now_iso(), error=str(e))
            if isinstance(runner, FlexSimRunner):  # 出錯就重開 FlexSim
                runner = make_runner(CONFIG)
        finally:
            with jobs_lock:
                history.insert(0, job_id)
                del history[CONFIG.get("history_size", 50):]
            try:
                (RESULTS_DIR / f"{job_id}.json").write_text(
                    json.dumps(jobs[job_id], ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            job_queue.task_done()


threading.Thread(target=worker, daemon=True, name="flexsim-worker").start()

app = FastAPI(title="FlexSim Web Runner")


def check_key(x_access_key: str | None) -> None:
    key = CONFIG.get("access_key") or ""
    if key and x_access_key != key:
        raise HTTPException(401, "Invalid access key")


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    if job["status"] == "queued":
        with jobs_lock:
            waiting = sorted((j for j in jobs.values() if j["status"] == "queued"), key=lambda j: j["submitted_at"])
        out["queue_position"] = next((i + 1 for i, j in enumerate(waiting) if j["id"] == job["id"]), None)
    return out


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/config")
def get_config():
    out = public_config(CONFIG)
    out["requires_key"] = bool(CONFIG.get("access_key"))
    return out


@app.post("/api/run")
def submit_run(req: RunRequest, x_access_key: str | None = Header(default=None)):
    check_key(x_access_key)
    try:
        params, length = validate_params(CONFIG, req.parameters, req.run_length)
    except ValueError as e:
        raise HTTPException(400, str(e))
    job_id = uuid.uuid4().hex[:10]
    job = {
        "id": job_id,
        "status": "queued",
        "label": (req.label or "").strip()[:60] or None,
        "submitted_by": (req.submitted_by or "").strip()[:60] or None,
        "parameters": params,
        "run_length": length,
        "submitted_at": now_iso(),
    }
    with jobs_lock:
        jobs[job_id] = job
    job_queue.put(job_id)
    log.info("Queued job %s", job_id)
    return public_job(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, x_access_key: str | None = Header(default=None)):
    check_key(x_access_key)
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return public_job(job)


@app.get("/api/history")
def get_history(x_access_key: str | None = Header(default=None)):
    check_key(x_access_key)
    with jobs_lock:
        return [jobs[i] for i in history if i in jobs]


@app.get("/api/status")
def status():
    with jobs_lock:
        running = sum(1 for j in jobs.values() if j["status"] == "running")
        queued = sum(1 for j in jobs.values() if j["status"] == "queued")
    return {"ok": True, "running": running, "queued": queued, "mock_mode": bool(CONFIG.get("mock_mode"))}

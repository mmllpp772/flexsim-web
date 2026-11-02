"""
FlexSim Web Runner
------------------
在桌機上啟動這個服務後，同一個公司網路裡的人（包含美國同事）
用瀏覽器打開 http://<你的電腦名稱或IP>:8000 就能輸入參數、執行 FlexSim 模擬、看指標。

架構：
    瀏覽器 (index.html)  --HTTP-->  這個 FastAPI 服務  --FlexSimPy-->  FlexSim

重點設計：
- FlexSim 一次只跑一個模擬：所有請求進同一個佇列，由單一背景執行緒依序處理。
- FlexSim 只啟動一次並保持開著，之後每次只做「設參數 → reset → 跑 → 讀指標」，速度快很多。
- mock_mode=true 時不碰 FlexSim，用假資料讓你先測網頁流程。
"""

from __future__ import annotations

import json
import logging
import math
import queue
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "server.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("flexsim-web")


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()


# ---------------------------------------------------------------------------
# 工作 (Job) 資料
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    parameters: dict[str, float]
    run_length: float | None = None
    label: str | None = None
    submitted_by: str | None = None


jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
job_queue: "queue.Queue[str]" = queue.Queue()
history: list[str] = []  # 已完成的 job id，新的在前


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_request(req: RunRequest) -> tuple[dict[str, float], float]:
    """檢查參數範圍，避免有人輸入奇怪的值把模型跑壞。"""
    clean: dict[str, float] = {}
    for p in CONFIG["parameters"]:
        name = p["name"]
        if name not in req.parameters:
            raise HTTPException(400, f"Missing parameter: {p['label']}")
        try:
            v = float(req.parameters[name])
        except (TypeError, ValueError):
            raise HTTPException(400, f"{p['label']} must be a number")
        if math.isnan(v) or math.isinf(v):
            raise HTTPException(400, f"{p['label']} must be a number")
        if "min" in p and v < p["min"]:
            raise HTTPException(400, f"{p['label']} must be ≥ {p['min']}")
        if "max" in p and v > p["max"]:
            raise HTTPException(400, f"{p['label']} must be ≤ {p['max']}")
        if p.get("type") == "integer":
            if v != int(v):
                raise HTTPException(400, f"{p['label']} must be a whole number")
            v = int(v)
        clean[name] = v

    rl = CONFIG["run_length"]
    length = float(req.run_length if req.run_length is not None else rl["default"])
    if length < rl["min"] or length > rl["max"]:
        raise HTTPException(400, f"{rl['label']} must be between {rl['min']} and {rl['max']} {rl['unit']}")
    return clean, length


# ---------------------------------------------------------------------------
# FlexSim 執行端
# ---------------------------------------------------------------------------

class FlexSimRunner:
    """包住 FlexSimPy。只能在背景工作執行緒裡使用（FlexSim 不支援多執行緒同時操作）。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.controller = None
        self.model_loaded = False

    def _ensure_started(self) -> None:
        if self.controller is not None and self.model_loaded:
            return
        import FlexSimPy  # 需要先依 README 安裝好 FlexSimPy 的 .pyd

        fcfg = self.cfg["flexsim"]
        kwargs = dict(fcfg.get("launch_kwargs", {}))
        kwargs.setdefault("programDir", fcfg["program_dir"])
        log.info("Launching FlexSim: %s", kwargs)
        self.controller = FlexSimPy.launch(**kwargs)
        log.info("Opening model: %s", fcfg["model_path"])
        self.controller.open(fcfg["model_path"])
        self.model_loaded = True

    def _eval(self, script: str) -> Any:
        return self.controller.evaluate(script)

    def run(self, params: dict[str, float], length_units: float) -> dict[str, Any]:
        self._ensure_started()
        rl = self.cfg["run_length"]
        stop_time = length_units * float(rl.get("seconds_per_unit", 1))

        # 1) 寫入參數
        for p in self.cfg["parameters"]:
            value = params[p["name"]]
            tmpl = p.get("set_script", "Model.parameters." + p["name"] + " = {value};")
            self._eval(tmpl.replace("{value}", repr(value)))

        # 2) reset（參數通常在 reset 時套用到模型）
        self.controller.reset()

        # 3) 全速執行到停止時間
        t0 = time.time()
        self.controller.runToTime(stop_time)
        # 某些版本 runToTime 可能不會等到跑完，這裡保險再等一下
        limit = float(self.cfg["flexsim"].get("max_run_seconds", 1800))
        while True:
            try:
                model_time = float(self._eval("Model.time"))
            except Exception:
                break
            if model_time >= stop_time - 1e-6:
                break
            if time.time() - t0 > limit:
                raise RuntimeError(f"Simulation exceeded {limit:.0f}s wall-clock limit")
            time.sleep(0.5)

        # 4) 讀取指標
        metrics: dict[str, Any] = {}
        for m in self.cfg["metrics"]:
            try:
                v = self._eval(m["script"])
                metrics[m["name"]] = float(v) if v is not None else None
            except Exception as e:  # 單一指標失敗不影響其他指標
                log.warning("Metric %s failed: %s", m["name"], e)
                metrics[m["name"]] = None

        return {"metrics": metrics, "model_time": stop_time, "wall_seconds": round(time.time() - t0, 2)}


class MockRunner:
    """沒有 FlexSim 時的假模擬，用簡單的排隊公式產生看起來合理的數字，方便測網頁。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def run(self, params: dict[str, float], length_units: float) -> dict[str, Any]:
        time.sleep(random.uniform(2, 4))  # 模擬執行時間
        rl = self.cfg["run_length"]
        horizon = length_units * float(rl.get("seconds_per_unit", 1))

        arr = float(params.get("ArrivalInterval", 30))
        proc = float(params.get("ProcessTime", 25))
        servers = max(1, int(params.get("NumOperators", 1)))
        lam, mu = 1 / arr, servers / proc
        rho = min(lam / mu, 0.99)
        throughput = min(lam, mu) * horizon * random.uniform(0.96, 1.02)
        wq = (rho / (1 - rho)) * (proc / servers) * random.uniform(0.85, 1.15)
        lq = lam * wq

        values = {
            "throughput": throughput,
            "throughput_per_hour": throughput / (horizon / 3600) if horizon else 0,
            "avg_wait": wq,
            "avg_queue": lq,
            "utilization": rho * 100,
        }
        metrics = {m["name"]: values.get(m["name"], random.uniform(0, 100)) for m in self.cfg["metrics"]}
        return {"metrics": metrics, "model_time": horizon, "wall_seconds": 3.0}


def make_runner():
    if CONFIG.get("mock_mode", False):
        log.warning("MOCK MODE: FlexSim is NOT being called. Set mock_mode=false in config.json.")
        return MockRunner(CONFIG)
    return FlexSimRunner(CONFIG)


def worker() -> None:
    runner = make_runner()
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
            # FlexSim 出錯時重新建立 runner，下一次會重新開模型
            if isinstance(runner, FlexSimRunner):
                runner = make_runner()
        finally:
            with jobs_lock:
                history.insert(0, job_id)
                del history[CONFIG.get("history_size", 50):]
            # 每次結果也存一份 JSON，方便事後追查
            try:
                (RESULTS_DIR / f"{job_id}.json").write_text(
                    json.dumps(jobs[job_id], ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
            job_queue.task_done()


threading.Thread(target=worker, daemon=True, name="flexsim-worker").start()


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

app = FastAPI(title="FlexSim Web Runner")


def check_key(x_access_key: str | None) -> None:
    key = CONFIG.get("access_key") or ""
    if key and x_access_key != key:
        raise HTTPException(401, "Invalid access key")


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    if job["status"] == "queued":
        with jobs_lock:
            waiting = [j for j in jobs.values() if j["status"] == "queued"]
        waiting.sort(key=lambda j: j["submitted_at"])
        out["queue_position"] = next((i + 1 for i, j in enumerate(waiting) if j["id"] == job["id"]), None)
    return out


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/api/config")
def get_config():
    keys = ["title", "subtitle", "run_length", "parameters", "metrics"]
    out = {k: CONFIG[k] for k in keys}
    out["parameters"] = [{k: v for k, v in p.items() if k != "set_script"} for p in CONFIG["parameters"]]
    out["metrics"] = [{k: v for k, v in m.items() if k != "script"} for m in CONFIG["metrics"]]
    out["requires_key"] = bool(CONFIG.get("access_key"))
    out["mock_mode"] = bool(CONFIG.get("mock_mode"))
    return out


@app.post("/api/run")
def submit_run(req: RunRequest, x_access_key: str | None = Header(default=None)):
    check_key(x_access_key)
    params, length = validate_request(req)
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

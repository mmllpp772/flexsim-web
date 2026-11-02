"""
OneDrive 中轉模式（不用開 port、不用管理員權限）
-----------------------------------------------
你的電腦和美國同事都同步同一個 OneDrive 共用資料夾：

    美國同事的網頁 ──寫入 jobs/xxx.json──> OneDrive ──同步──> 你的電腦
    你的電腦跑 FlexSim ──寫入 results/xxx.json──> OneDrive ──同步──> 網頁顯示指標

共用資料夾裡的結構（由這支程式自動建立）：
    Simulator.html   給美國同事開的網頁（每次啟動自動更新）
    config.json      網頁用的公開設定（不含模型路徑和 FlexScript）
    status.json      心跳：讓網頁知道模擬電腦是否在線、排隊狀況
    jobs/            網頁送來的工作
    results/         模擬結果

啟動：python onedrive_worker.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from runner import (BASE_DIR, FlexSimRunner, load_config, log, make_runner, now_iso,
                    public_config, setup_logging, validate_params)

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_JOB_BYTES = 64 * 1024
FINAL = {"done", "error"}


def write_json_atomic(path: Path, data: Any) -> None:
    """先寫暫存檔再改名，避免 OneDrive 同步到寫一半的檔案。
    暫存檔以 ~$ 開頭，OneDrive 不會同步這類檔案。"""
    tmp = path.with_name(f"~${path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(10):  # OneDrive 同步中可能短暫鎖住檔案，稍等重試
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.5)


def read_json(path: Path) -> Any | None:
    try:
        if path.stat().st_size > MAX_JOB_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None  # 可能還在同步中，下一輪再試


def resolve_share_dir(share: str) -> Path:
    """支援 %OneDriveCommercial% 這類 Windows 環境變數。
    公司 OneDrive 的位置會存在 OneDriveCommercial（例如 C:\\Users\\工號\\OneDrive - 公司名稱），
    所以 config 寫 %OneDriveCommercial%/FlexSimShare，每台公司電腦都能自動找到，不用填工號。"""
    def sub(m: re.Match) -> str:
        name = m.group(1)
        val = os.environ.get(name)
        if not val and name.lower() == "onedrivecommercial":
            val = os.environ.get("OneDrive")  # 沒有公司版時退回一般 OneDrive
        if not val:
            raise SystemExit(f"找不到環境變數 %{name}%（這台電腦可能沒有登入公司 OneDrive）。\n"
                             f"請在 config.json 的 share_dir 直接填資料夾完整路徑。")
        return val
    return Path(os.path.expanduser(re.sub(r"%([^%]+)%", sub, share)))


class Worker:
    def __init__(self) -> None:
        self.cfg = load_config()
        od = self.cfg.get("onedrive", {})
        share = od.get("share_dir", "")
        if not share:
            raise SystemExit('config.json 裡 "onedrive" → "share_dir" 還沒設定 OneDrive 共用資料夾路徑')
        self.share = resolve_share_dir(share)
        if not self.share.exists():
            if self.share.parent.is_dir():  # OneDrive 在，只是還沒建資料夾 → 自動建立
                self.share.mkdir()
                log.info("Created shared folder %s — remember to share it with your colleagues in OneDrive.", self.share)
            else:
                raise SystemExit(f"找不到共用資料夾：{self.share}\n請確認 OneDrive 已同步這個資料夾，或在 config.json 的 share_dir 填入完整路徑")
        self.poll = float(od.get("poll_seconds", 3))
        self.heartbeat_every = float(od.get("heartbeat_seconds", 30))
        self.jobs_dir = self.share / "jobs"
        self.results_dir = self.share / "results"
        self.jobs_dir.mkdir(exist_ok=True)
        self.results_dir.mkdir(exist_ok=True)
        self.runner = make_runner(self.cfg)
        self.bad_seen: dict[str, float] = {}  # 讀不到的檔案第一次出現的時間
        self.queue: list[str] = []
        self.running: str | None = None
        self.last_heartbeat = 0.0
        self.stopping = False

    # ---------- 共用資料夾初始化 ----------
    def publish(self) -> None:
        write_json_atomic(self.share / "config.json", public_config(self.cfg))
        page = BASE_DIR / "index.html"
        target = self.share / "Simulator.html"
        if page.exists():
            if not target.exists() or target.read_bytes() != page.read_bytes():
                shutil.copyfile(page, target)
                log.info("Published %s", target)

    def heartbeat(self, force: bool = False, online: bool = True) -> None:
        if not force and time.time() - self.last_heartbeat < self.heartbeat_every:
            return
        write_json_atomic(self.share / "status.json", {
            "online": online,
            "updated_at": now_iso(),
            "running": self.running,
            "queue": self.queue,
            "mock_mode": bool(self.cfg.get("mock_mode")),
            "poll_seconds": self.poll,
            "heartbeat_seconds": self.heartbeat_every,
        })
        self.last_heartbeat = time.time()

    # ---------- 找出待處理的工作 ----------
    def scan(self) -> list[str]:
        pending: list[str] = []
        for f in self.jobs_dir.glob("*.json"):
            job_id = f.stem
            if not ID_RE.match(job_id):
                continue
            res = read_json(self.results_dir / f"{job_id}.json")
            if res and res.get("status") in FINAL:
                continue
            pending.append(job_id)
        return sorted(pending)  # id 以時間開頭，排序後就是先來先做

    def write_result(self, job_id: str, data: dict[str, Any]) -> None:
        write_json_atomic(self.results_dir / f"{job_id}.json", data)

    def process(self, job_id: str) -> None:
        path = self.jobs_dir / f"{job_id}.json"
        job = read_json(path)
        if job is None:
            first = self.bad_seen.setdefault(job_id, time.time())
            if time.time() - first > 300:  # 5 分鐘還讀不到，視為壞檔
                self.write_result(job_id, {"id": job_id, "status": "error", "finished_at": now_iso(),
                                           "error": "Job file could not be read"})
            return
        self.bad_seen.pop(job_id, None)

        base = {
            "id": job_id,
            "label": str(job.get("label") or "")[:60] or None,
            "submitted_by": str(job.get("submitted_by") or "")[:60] or None,
            "submitted_at": job.get("submitted_at"),
        }
        try:
            params, length = validate_params(self.cfg, job.get("parameters"), job.get("run_length"))
        except ValueError as e:
            self.write_result(job_id, {**base, "status": "error", "finished_at": now_iso(), "error": str(e),
                                       "parameters": job.get("parameters"), "run_length": job.get("run_length")})
            log.warning("Rejected job %s: %s", job_id, e)
            return

        base.update(parameters=params, run_length=length)
        self.running = job_id
        self.write_result(job_id, {**base, "status": "running", "started_at": now_iso()})
        self.heartbeat(force=True)
        log.info("Running job %s %s", job_id, params)
        started = now_iso()
        try:
            result = self.runner.run(params, length)
            self.write_result(job_id, {**base, "status": "done", "started_at": started,
                                       "finished_at": now_iso(), **result})
            log.info("Job %s done", job_id)
        except Exception as e:
            log.exception("Job %s failed", job_id)
            self.write_result(job_id, {**base, "status": "error", "started_at": started,
                                       "finished_at": now_iso(), "error": str(e)})
            if isinstance(self.runner, FlexSimRunner):  # 出錯就重開 FlexSim
                self.runner = make_runner(self.cfg)
        finally:
            self.running = None

    # ---------- 主迴圈 ----------
    def loop(self) -> None:
        self.publish()
        log.info("Watching %s (every %.0fs). Press Ctrl+C to stop.", self.share, self.poll)
        while not self.stopping:
            try:
                self.queue = self.scan()
                self.heartbeat(force=bool(self.queue))
                if self.queue:
                    self.process(self.queue.pop(0))
                    self.heartbeat(force=True)  # 讓網頁馬上知道已經跑完
                    continue  # 立刻看下一個
            except Exception:
                log.exception("Loop error")
            time.sleep(self.poll)
        self.heartbeat(force=True, online=False)
        log.info("Stopped.")


def main() -> None:
    setup_logging("worker.log")
    w = Worker()

    def stop(*_):
        w.stopping = True
    signal.signal(signal.SIGINT, stop)
    try:
        signal.signal(signal.SIGTERM, stop)
    except Exception:
        pass
    w.loop()


if __name__ == "__main__":
    main()

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

from runner import (BASE_DIR, Cancelled, load_config, model_datetime, log, make_runner, now_iso,
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
            # 有些電腦沒有設定 OneDriveCommercial：直接找家目錄下的「OneDrive - 公司名稱」資料夾
            company = sorted(p for p in Path.home().glob("OneDrive - *") if p.is_dir())
            if len(company) == 1:
                val = str(company[0])
            elif len(company) > 1:
                raise SystemExit("找到多個公司 OneDrive 資料夾：\n  " + "\n  ".join(map(str, company)) +
                                 "\n請在 config.json 的 share_dir 填入要用的完整路徑。")
        if not val:
            raise SystemExit(f"找不到 %{name}%（這台電腦可能沒有登入公司 OneDrive）。\n"
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
        self.cancel_dir = self.share / "cancel"
        for d in (self.jobs_dir, self.results_dir, self.cancel_dir):
            d.mkdir(exist_ok=True)
        try:
            self.runner = make_runner(self.cfg)
        except RuntimeError as e:
            raise SystemExit(f"無法啟動 FlexSim：{e}")
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
            if (self.cancel_dir / f"{job_id}.json").exists():
                job = read_json(f) or {}
                self.write_result(job_id, {**self.base_info(job_id, job), "parameters": job.get("parameters"),
                                           "status": "error", "finished_at": now_iso(),
                                           "error": "Cancelled before it started"})
                log.info("Job %s cancelled before start", job_id)
                continue
            pending.append(job_id)
        return sorted(pending)  # id 以時間開頭，排序後就是先來先做

    @staticmethod
    def base_info(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job_id,
            "label": str(job.get("label") or "")[:60] or None,
            "submitted_by": str(job.get("submitted_by") or "")[:60] or None,
            "submitted_at": job.get("submitted_at"),
            "mode": "fast" if job.get("mode") == "fast" else "run",
        }

    def recover_interrupted(self) -> None:
        """上次程式中斷時正在跑的工作：標記為中斷，不要自動重跑。"""
        for f in self.results_dir.glob("*.json"):
            res = read_json(f)
            if res and res.get("status") == "running":
                res.update(status="error", finished_at=now_iso(),
                           error="Interrupted: the simulator PC stopped before this run finished. Please run it again.")
                res.pop("progress", None)
                res.pop("progress_date", None)
                self.write_result(f.stem, res)
                log.warning("Marked interrupted job %s", f.stem)

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

        base = self.base_info(job_id, job)
        try:
            params, length = validate_params(self.cfg, job.get("parameters"), job.get("run_length"))
        except ValueError as e:
            self.write_result(job_id, {**base, "status": "error", "finished_at": now_iso(), "error": str(e),
                                       "parameters": job.get("parameters"), "run_length": job.get("run_length")})
            log.warning("Rejected job %s: %s", job_id, e)
            return

        base.update(parameters=params, run_length=length)
        self.running = job_id
        started = now_iso()
        self.write_result(job_id, {**base, "status": "running", "started_at": started})
        self.heartbeat(force=True)
        log.info("Running job %s %s", job_id, params)
        cancel_file = self.cancel_dir / f"{job_id}.json"
        last_write = [0.0]

        def should_cancel() -> str | None:
            if self.stopping:
                return "Stopped on the simulator PC before it finished. Please run it again."
            if cancel_file.exists():
                return "Cancelled"
            return None

        def on_progress(fraction: float, model_time: float) -> None:
            if time.time() - last_write[0] < 10:  # 最多 10 秒寫一次，避免 OneDrive 一直同步
                return
            last_write[0] = time.time()
            dt = model_datetime(self.cfg, model_time)
            self.write_result(job_id, {**base, "status": "running", "started_at": started,
                                       "progress": round(fraction, 4),
                                       "progress_date": dt.strftime("%Y-%m-%d %H:%M") if dt else None})
            log.info("Job %s: %.0f%%%s", job_id, fraction * 100, f" (simulated to {dt:%Y-%m-%d})" if dt else "")

        try:
            result = self.runner.run(params, length, should_cancel=should_cancel,
                                     on_progress=None if base["mode"] == "fast" else on_progress)
            self.write_result(job_id, {**base, "status": "done", "started_at": started,
                                       "finished_at": now_iso(), **result})
            log.info("Job %s done", job_id)
        except Cancelled as e:
            log.warning("Job %s: %s", job_id, e)
            self.write_result(job_id, {**base, "status": "error", "started_at": started,
                                       "finished_at": now_iso(), "error": str(e)})
        except Exception as e:
            log.exception("Job %s failed", job_id)
            self.write_result(job_id, {**base, "status": "error", "started_at": started,
                                       "finished_at": now_iso(), "error": str(e)})

        finally:
            self.running = None

    # ---------- 主迴圈 ----------
    def loop(self) -> None:
        self.publish()
        self.recover_interrupted()
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
        if w.stopping:  # 第二次 Ctrl+C：立刻結束
            raise KeyboardInterrupt
        w.stopping = True
        log.info("Stopping… (closing FlexSim if a run is in progress)")
    signal.signal(signal.SIGINT, stop)
    try:
        signal.signal(signal.SIGTERM, stop)
    except Exception:
        pass
    w.loop()


if __name__ == "__main__":
    main()

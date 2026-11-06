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
from datetime import datetime
from pathlib import Path
from typing import Any

import hashlib
import hmac

from schedule import validate_plans, validate_shifts
import ctfpyr

CACHE_VERSION = 2  # model_defaults.json 的格式；改了就會重新從模型讀一次
from runner import (BASE_DIR, Cancelled, load_config, model_datetime, period_of, log, make_runner, now_iso,
                    public_config, setup_logging, validate_params)

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_JOB_BYTES = 2 * 1024 * 1024      # 網頁送來的檔案（工作、控制、管理要求）上限
MAX_RESULT_BYTES = 50 * 1024 * 1024  # 自己寫的結果檔（含每日表格）上限
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


def read_json(path: Path, limit: int = MAX_JOB_BYTES) -> Any | None:
    try:
        if path.stat().st_size > limit:
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
        self.control_dir = self.share / "control"
        self.admin_dir = self.share / "admin"
        self.admin_fails: list[float] = []
        for d in (self.jobs_dir, self.results_dir, self.cancel_dir, self.control_dir, self.admin_dir):
            d.mkdir(exist_ok=True)
        try:
            self.runner = make_runner(self.cfg)
        except RuntimeError as e:
            raise SystemExit(f"無法啟動 FlexSim：{e}")
        self.ct_failed_at = 0.0
        self.param_defaults: dict[str, Any] | None = None
        self.dist_defaults: dict[str, Any] | None = None
        self.ct_defaults = self.load_ct_defaults()
        self.bad_seen: dict[str, float] = {}  # 讀不到的檔案第一次出現的時間
        self.queue: list[str] = []
        self.running: str | None = None
        self.last_heartbeat = 0.0
        self.stopping = False

    # ---------- CT / FPYR 預設值（從模型讀，模型檔有變才重讀） ----------
    def load_ct_defaults(self) -> dict[str, Any] | None:
        names = ctfpyr.tables_to_read(self.cfg)
        if not names:
            return None
        cache = BASE_DIR / "model_defaults.json"
        model = Path(self.cfg["flexsim"]["model_path"])
        if not self.cfg.get("mock_mode"):
            cached = ctfpyr.load_cached(cache, model)
            if cached and cached.get("v") == CACHE_VERSION:
                self.param_defaults = cached.get("params")
                self.dist_defaults = cached.get("dist")
                return cached["ct"]
        log.info("Reading CT and FPYR from the model (once per model change, about a minute)...")
        try:
            tables = self.runner.read_tables(names)
            defaults = ctfpyr.build_defaults(self.cfg, tables)
            self.param_defaults = ctfpyr.build_param_defaults(self.cfg, tables)
            self.dist_defaults = ctfpyr.build_dist_defaults(self.cfg, tables)
        except Exception as e:  # 讀不到就不提供 CT/FPYR 輸入，其他功能照常；10 分鐘後再試
            log.warning("Could not read CT/FPYR from the model: %s", e)
            self.ct_failed_at = time.time()
            return None
        if not self.cfg.get("mock_mode"):
            ctfpyr.save_cached(cache, model, {"v": CACHE_VERSION, "ct": defaults, "params": self.param_defaults,
                                                 "dist": self.dist_defaults})
        n = sum(len(m) for d in defaults.values() for m in d["machines"].values())
        log.info("CT/FPYR: %d machines read from the model", n)
        return defaults

    def refresh_ct_defaults(self) -> None:
        """模型檔被更新時（例如存了新版本），重新讀預設值並發布。"""
        if self.cfg.get("mock_mode") or not ctfpyr.tables_to_read(self.cfg):
            return
        if time.time() - self.ct_failed_at < 600:
            return
        model = Path(self.cfg["flexsim"]["model_path"])
        cached = ctfpyr.load_cached(BASE_DIR / "model_defaults.json", model)
        if cached is None or cached.get("v") != CACHE_VERSION:
            self.ct_defaults = self.load_ct_defaults()
            self.publish()

    # ---------- 共用資料夾初始化 ----------
    def publish(self) -> None:
        pub = public_config(self.cfg)
        if self.ct_defaults and self.cfg.get("ct_fpyr"):
            cf = self.cfg["ct_fpyr"]
            pub["ct_fpyr"] = {"stations": [s["name"] for s in cf["stations"]],
                              "fpyr": [s["name"] for s in cf["stations"] if s.get("fpyr")],
                              "defaults": self.ct_defaults,
                              "bottleneck_only": [s["name"] for s in cf["stations"] if s.get("bottleneck_only")],
                              "dist": {n: d["value"] for n, d in (self.dist_defaults or {}).items()}}
        if self.param_defaults and self.cfg.get("param_tables"):
            pub["param_tables"] = [{**{k: v for k, v in g.items() if not k.startswith("_")},
                                    "defaults": self.param_defaults.get(g["name"], {})} for g in self.cfg["param_tables"]]
        # 3D 佈置圖：config 的 layout3d.file，在程式資料夾或上一層找到就複製到共用資料夾
        l3d = (self.cfg.get("layout3d") or {}).get("file")
        if l3d:
            src = next((d / l3d for d in (BASE_DIR, BASE_DIR.parent) if (d / l3d).is_file()), None)
            if src:
                dst = self.share / Path(l3d).name
                try:
                    if not dst.exists() or dst.stat().st_size != src.stat().st_size or dst.stat().st_mtime < src.stat().st_mtime:
                        shutil.copyfile(src, dst)
                        log.info("Published 3D layout %s", dst)
                    pub["layout3d_file"] = dst.name
                except OSError as e:
                    log.warning("Could not copy the 3D layout: %s", e)
            elif (self.share / Path(l3d).name).is_file():
                pub["layout3d_file"] = Path(l3d).name
            else:
                log.warning("3D layout %s not found next to the program", l3d)
        write_json_atomic(self.share / "config.json", pub)
        page = BASE_DIR / "index.html"
        target = self.share / "Simulator.html"
        if page.exists():
            if not target.exists() or target.read_bytes() != page.read_bytes():
                shutil.copyfile(page, target)
                log.info("Published %s", target)
        # PDF 報告用的程式庫（jsPDF、html2canvas），放在 Simulator.html 旁邊的 vendor 資料夾
        vendor = BASE_DIR / "vendor"
        if vendor.is_dir():
            dest = self.share / "vendor"
            dest.mkdir(exist_ok=True)
            for f in vendor.glob("*.js"):
                t = dest / f.name
                if not t.exists() or t.stat().st_size != f.stat().st_size:
                    shutil.copyfile(f, t)
                    log.info("Published %s", t)

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
            res = read_json(self.results_dir / f"{job_id}.json", MAX_RESULT_BYTES)
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
            "client": Worker.client_info(job.get("client")),
        }

    @staticmethod
    def client_info(c: Any) -> dict[str, str] | None:
        """送出者的裝置資訊（網頁直接開檔案，拿不到 IP；只保留這幾個欄位，並限制長度）。"""
        if not isinstance(c, dict):
            return None
        keys = ("device_id", "os", "browser", "timezone", "utc_offset", "language", "local_time")
        return {k: str(c.get(k))[:60] for k in keys if c.get(k) is not None}

    def recover_interrupted(self) -> None:
        """上次程式中斷時正在跑的工作：標記為中斷，不要自動重跑。"""
        for f in self.results_dir.glob("*.json"):
            res = read_json(f, MAX_RESULT_BYTES)
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
            period = period_of(self.cfg, params)
            plans = validate_plans(self.cfg, job.get("plans"), *period) if period else None
            shifts = validate_shifts(self.cfg, job.get("shifts"))
            ct = ctfpyr.validate(self.cfg, job.get("ct"), self.ct_defaults)
            ct_dist = ctfpyr.validate_dist(job.get("ct_dist"), self.dist_defaults)
            ptab = ctfpyr.validate_params_tables(self.cfg, job.get("param_tables"), self.param_defaults)
        except ValueError as e:
            self.write_result(job_id, {**base, "status": "error", "finished_at": now_iso(), "error": str(e),
                                       "parameters": job.get("parameters"), "run_length": job.get("run_length")})
            log.warning("Rejected job %s: %s", job_id, e)
            return

        # 沒有機台表、只填瓶頸 CT 的站別（例如 POD）：記下這次實際用的 CT，網頁算 OEE / Takt 用
        station_ct = None
        if self.ct_defaults and self.cfg.get("ct_fpyr"):
            only = [st["name"] for st in self.cfg["ct_fpyr"]["stations"] if st.get("bottleneck_only")]
            station_ct = {prod: {n: (ct or {}).get(prod, {}).get("stations", {}).get(n, {}).get("ct", d["stations"].get(n, {}).get("ct"))
                                 for n in only} for prod, d in self.ct_defaults.items()} if only else None
        base.update(parameters=params, run_length=length, plans=plans, shifts=shifts, ct=ct, ct_dist=ct_dist, param_tables=ptab,
                    station_ct=station_ct)
        self.running = job_id
        started = now_iso()
        t_start = time.time()
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

        series: list[dict[str, Any]] = []  # 執行中的指標變化，給網頁畫小折線圖
        control_file = self.control_dir / f"{job_id}.json"
        st = {"mode": base["mode"], "paused": False, "last": None}  # last = 最新的進度快照

        def add_point(model_time: float, metrics: dict[str, Any]) -> None:
            dt = model_datetime(self.cfg, params, model_time)
            series.append({"t": model_time, "date": dt.strftime("%Y-%m-%d %H:%M") if dt else None, "m": metrics})
            if len(series) > 200:  # 太多點就隔一個刪一個
                del series[1:-1:2]

        def publish() -> None:
            """把目前狀態寫給網頁。快轉模式回報進度百分比和日期，但不回報即時指標。"""
            data = {**base, "mode": st["mode"], "status": "running", "started_at": started, "paused": st["paused"]}
            snap = st["last"]
            if snap:
                fraction, model_time, metrics = snap
                dt = model_datetime(self.cfg, params, model_time)
                data.update(progress=round(fraction, 4), progress_date=dt.strftime("%Y-%m-%d %H:%M") if dt else None)
                if st["mode"] != "fast":
                    data.update(live_metrics=metrics or None, series=series)
            self.write_result(job_id, data)
            last_write[0] = time.time()

        def want_pause() -> bool:
            """讀網頁送來的控制指令：pause / run / fast"""
            ctl = read_json(control_file) if control_file.exists() else None
            action = (ctl or {}).get("action")
            if action in ("run", "fast") and action != st["mode"]:
                st["mode"] = action
                log.info("Job %s: switched to %s", job_id, action)
                if not st["paused"] or action:  # 恢復時也一併換模式
                    publish()
            return action == "pause"

        def on_pause(paused: bool) -> None:
            st["paused"] = paused
            log.info("Job %s %s", job_id, "paused" if paused else "resumed")
            publish()  # 狀態改變就馬上寫，不等 10 秒

        def on_progress(fraction: float, model_time: float, metrics: dict[str, Any]) -> None:
            st["last"] = (fraction, model_time, metrics)
            if time.time() - last_write[0] < 10:  # 最多 10 秒寫一次，避免 OneDrive 一直同步
                return
            if metrics and st["mode"] != "fast":
                add_point(model_time, metrics)
            publish()
            dt = model_datetime(self.cfg, params, model_time)
            log.info("Job %s: %.0f%%%s", job_id, fraction * 100, f" (simulated to {dt:%Y-%m-%d})" if dt else "")

        try:
            result = self.runner.run(params, length, should_cancel=should_cancel, on_progress=on_progress,
                                     want_pause=want_pause, on_pause=on_pause, plans=plans, shifts=shifts,
                                     ct=ct, ct_defaults=self.ct_defaults, param_tables=ptab,
                                     ct_dist=ct_dist, dist_defaults=self.dist_defaults)
            base["mode"] = st["mode"]
            if series and result.get("model_time") is not None:
                add_point(result["model_time"], result["metrics"])
            self.write_result(job_id, {**base, "status": "done", "started_at": started,
                                       "finished_at": now_iso(), **result, "series": series or None})
            log.info("Job %s done", job_id)
        except Cancelled as e:
            log.warning("Job %s: %s", job_id, e)
            self.write_result(job_id, {**base, "status": "error", "started_at": started,
                                       "finished_at": now_iso(), "error": str(e),
                                       "wall_seconds": round(time.time() - t_start, 1)})
        except Exception as e:
            log.exception("Job %s failed", job_id)
            self.write_result(job_id, {**base, "status": "error", "started_at": started,
                                       "finished_at": now_iso(), "error": str(e),
                                       "wall_seconds": round(time.time() - t_start, 1)})

        finally:
            self.running = None

    # ---------- 管理：清除執行紀錄 ----------
    def handle_admin(self) -> None:
        for f in sorted(self.admin_dir.glob("*.json")):
            if f.name.endswith(".result.json") or not ID_RE.match(f.stem):
                continue
            req = read_json(f)
            if req is None:
                continue
            res = self.admin_request(f.stem, req)
            write_json_atomic(self.admin_dir / f"{f.stem}.result.json", {**res, "at": now_iso()})
            f.unlink(missing_ok=True)  # 要求檔處理完就刪掉

    def admin_request(self, req_id: str, req: dict[str, Any]) -> dict[str, Any]:
        code = str(self.cfg.get("admin_code") or "").strip()
        if req.get("action") != "clear_history":
            return {"status": "bad_request"}
        if not code:
            return {"status": "disabled"}
        now = time.time()
        self.admin_fails = [t for t in self.admin_fails if now - t < 900]
        if len(self.admin_fails) >= 5:
            return {"status": "locked"}
        expect = hashlib.sha256(f"{code}:{req_id}".encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expect, str(req.get("hash", ""))):
            self.admin_fails.append(now)
            log.warning("Admin request %s: wrong code", req_id)
            return {"status": "bad_code"}
        if self.running:
            return {"status": "busy"}
        # 封存（搬到這台電腦的 history_archive/），不直接刪除
        dest = BASE_DIR / "history_archive" / datetime.now().strftime("%Y%m%d-%H%M%S")
        n = 0
        for d in (self.jobs_dir, self.results_dir, self.cancel_dir, self.control_dir):
            for f in d.glob("*.json"):
                (dest / d.name).mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dest / d.name / f.name))
                if d is self.jobs_dir:
                    n += 1
        log.info("History cleared by %s: %d runs archived to %s", req.get("by") or "?", n, dest)
        return {"status": "ok", "archived": n}

    # ---------- 主迴圈 ----------
    def loop(self) -> None:
        self.publish()
        self.recover_interrupted()
        log.info("Watching %s (every %.0fs). Press Ctrl+C to stop.", self.share, self.poll)
        while not self.stopping:
            try:
                self.handle_admin()
                self.queue = self.scan()
                if not self.queue:
                    self.refresh_ct_defaults()
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

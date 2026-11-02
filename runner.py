"""
模擬執行核心（onedrive_worker.py 使用）
=====================================

用「命令列」控制 FlexSim，不需要 FlexSimPy、不需要編譯任何東西：

    每一個工作：
    1. 產生一個 FlexScript 腳本檔（寫入參數 → reset → 跑到結束時間 → 把指標寫成文字檔 → 關閉 FlexSim）
    2. 執行  flexsim.exe 模型.fsm /maintenance nogui_disablemsg_runscript /scriptpath 腳本檔
    3. 讀回指標文字檔

模型檔本身不會被修改或存檔。
"""

from __future__ import annotations

import glob
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
RUNS_DIR = BASE_DIR / "runs"

log = logging.getLogger("flexsim-web")


def setup_logging(filename: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(BASE_DIR / filename, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """給網頁看的設定：不含 FlexSim 路徑、模型路徑、FlexScript。"""
    out = {k: cfg.get(k) for k in ["title", "title_zh", "subtitle", "subtitle_zh", "run_length", "parameters", "metrics"]}
    out["parameters"] = [{k: v for k, v in p.items() if k != "set_script"} for p in cfg["parameters"]]
    out["metrics"] = [{k: v for k, v in m.items() if k != "script"} for m in cfg["metrics"]]
    out["mock_mode"] = bool(cfg.get("mock_mode"))
    di = cfg.get("date_inputs")
    if di:
        out["date_inputs"] = dict(di)
        st = cfg.get("stop") or {}
        if st.get("model_start"):
            out["date_inputs"]["min"] = st["model_start"][:10]
    return out


# ---------------------------------------------------------------------------
# 參數檢查
# ---------------------------------------------------------------------------

def validate_params(cfg: dict[str, Any], parameters: Any, run_length: Any) -> tuple[dict[str, float], float | None]:
    """回傳 (乾淨的參數, 模擬長度或 None)；不合法時丟出 ValueError（英文訊息，直接顯示給使用者）。"""
    if not isinstance(parameters, dict):
        raise ValueError("Parameters are missing")
    clean: dict[str, float] = {}
    for p in cfg["parameters"]:
        name, label = p["name"], p["label"]
        if name not in parameters:
            raise ValueError(f"Missing parameter: {label}")
        raw = parameters[name]
        if isinstance(raw, bool):
            raise ValueError(f"{label} must be a number")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number")
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"{label} must be a number")
        if "min" in p and v < p["min"]:
            raise ValueError(f"{label} must be ≥ {p['min']}")
        if "max" in p and v > p["max"]:
            raise ValueError(f"{label} must be ≤ {p['max']}")
        if p.get("type") == "integer":
            if v != int(v):
                raise ValueError(f"{label} must be a whole number")
            v = int(v)
        clean[name] = v

    length: float | None = None
    rl = cfg.get("run_length")
    if rl:
        try:
            length = float(run_length if run_length is not None else rl["default"])
        except (TypeError, ValueError):
            raise ValueError(f"{rl['label']} must be a number")
        if math.isnan(length) or length < rl["min"] or length > rl["max"]:
            raise ValueError(f"{rl['label']} must be between {rl['min']} and {rl['max']} {rl['unit']}")

    stop_time(cfg, clean, length)  # 日期不合理時會在這裡擋下
    return clean, length


def _date_from(params: dict[str, float], year_key: str, month_key: str, day_key: str, what: str) -> date:
    try:
        return date(int(params[year_key]), int(params[month_key]), int(params[day_key]))
    except ValueError:
        raise ValueError(f"{what} date is not a valid calendar date")


def stop_time(cfg: dict[str, Any], params: dict[str, float], length: float | None) -> float:
    """算出要跑到的模型時間（模型時間單位）。"""
    st = cfg.get("stop", {"type": "run_length"})
    unit = float(st.get("time_unit_seconds", 1))
    if st["type"] == "end_of_date":
        model_start = datetime.fromisoformat(st["model_start"])
        y = st["year_param"]
        if st.get("start_month_param"):
            start = _date_from(params, y, st["start_month_param"], st["start_day_param"], "Start")
        else:
            start = None
        end = _date_from(params, y, st["end_month_param"], st["end_day_param"], "End")
        if start and end < start:
            raise ValueError("End date must be on or after the start date")
        end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())  # 結束日當天跑完
        seconds = (end_dt - model_start).total_seconds()
        if seconds <= 0:
            raise ValueError(f"End date must be after the model start ({model_start:%Y-%m-%d %H:%M})")
        return seconds / unit
    if st["type"] == "fixed":
        return float(st["time"])
    # run_length：網頁輸入的長度 × 每單位秒數
    rl = cfg["run_length"]
    return float(length) * float(rl.get("seconds_per_unit", 1)) / unit


# ---------------------------------------------------------------------------
# FlexSim 命令列執行
# ---------------------------------------------------------------------------

def find_flexsim_exe(cfg: dict[str, Any]) -> Path:
    exe = (cfg["flexsim"].get("exe") or "").strip()
    if exe:
        p = Path(exe)
        if not p.is_file():
            raise RuntimeError(f"flexsim.exe not found at {p} (check \"exe\" in config.json)")
        return p
    patterns = [
        r"C:\Program Files\FlexSim*\program\flexsim.exe",
        r"C:\Program Files (x86)\FlexSim*\program\flexsim.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\FlexSim*\program\flexsim.exe"),
    ]
    found = sorted({Path(f) for pat in patterns for f in glob.glob(pat)}, key=lambda p: p.stat().st_mtime, reverse=True)
    if not found:
        raise RuntimeError("Could not find flexsim.exe automatically. Set \"exe\" in config.json.")
    return found[0]


def _fs_str(s: str) -> str:
    """Python 字串 → FlexScript 字串常值"""
    return '"' + s.replace("\\", "/").replace('"', '\\"') + '"'


def build_script(cfg: dict[str, Any], params: dict[str, float], end_time: float, result_path: Path,
                 progress_path: Path | None) -> str:
    lines = ["// Generated by flexsim-web. Do not edit."]
    for p in cfg["parameters"]:
        value = params[p["name"]]
        tmpl = p.get("set_script", "Model.parameters." + p["name"] + " = {value};")
        lines.append(tmpl.replace("{value}", repr(value)))
    lines += [
        "resetmodel();",
        f"double wr_end = {end_time!r};",
        "double wr_last = -1;",
        "int wr_same = 0;",
        "int wr_n = 0;",
    ]
    if progress_path:
        lines.append(f"fileopen({_fs_str(str(progress_path))}, \"w\"); fpt(\"0\"); fileclose();")
    lines += [
        "while (Model.time < wr_end) {",
        "    step();",
    ]
    if progress_path:
        lines += [
            "    wr_n++;",
            "    if (wr_n % 20000 == 0) {",
            f"        fileopen({_fs_str(str(progress_path))}, \"w\"); fpt(numtostring(Model.time, 0, 0)); fileclose();",
            "    }",
        ]
    lines += [
        "    if (Model.time == wr_last) {",
        "        wr_same++;",
        "        if (wr_same > 2000000) break;  // no more events: avoid an endless loop",
        "    } else {",
        "        wr_same = 0;",
        "        wr_last = Model.time;",
        "    }",
        "}",
        f"fileopen({_fs_str(str(result_path))}, \"w\");",
        'fpt("model_time\\t" + numtostring(Model.time, 0, 6)); fpr();',
    ]
    for m in cfg["metrics"]:
        lines.append(f'fpt("{m["name"]}\\t" + numtostring({m["script"]}, 0, 6)); fpr();')
    lines += ['fpt("__done\\t1"); fpr();', "fileclose();", "cmdexit();"]
    return "\n".join(lines) + "\n"


def parse_results(path: Path) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "\t" not in line:
            continue
        k, v = line.split("\t", 1)
        try:
            out[k.strip()] = float(v.strip())
        except ValueError:
            out[k.strip()] = None
    return out


class Cancelled(Exception):
    """工作被取消（網頁按 Cancel，或模擬電腦按 Ctrl+C）。"""


def model_datetime(cfg: dict[str, Any], model_time: float) -> datetime | None:
    st = cfg.get("stop") or {}
    if not st.get("model_start"):
        return None
    return datetime.fromisoformat(st["model_start"]) + timedelta(seconds=model_time * float(st.get("time_unit_seconds", 1)))


class FlexSimRunner:
    """每個工作開一次 FlexSim（背景、無視窗），跑完自動關閉。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.exe = find_flexsim_exe(cfg)
        self.model = Path(cfg["flexsim"]["model_path"])
        if not self.model.is_file():
            raise RuntimeError(f"Model not found: {self.model}")
        RUNS_DIR.mkdir(exist_ok=True)
        log.info("FlexSim: %s", self.exe)
        log.info("Model:   %s", self.model)

    def run(self, params: dict[str, float], length: float | None,
            should_cancel: Callable[[], str | None] | None = None,
            on_progress: Callable[[float, float], None] | None = None) -> dict[str, Any]:
        end_time = stop_time(self.cfg, params, length)
        run_dir = RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir.mkdir(parents=True)
        script = run_dir / "script.txt"
        result = run_dir / "result.txt"
        progress = run_dir / "progress.txt"
        script.write_text(build_script(self.cfg, params, end_time, result, progress if on_progress else None),
                          encoding="utf-8")

        cmd = [str(self.exe), str(self.model), "/maintenance", "nogui_disablemsg_runscript",
               "/scriptpath", str(script)]
        limit = float(self.cfg["flexsim"].get("max_run_seconds", 1800))
        log.info("Launching FlexSim (end time %.0f)", end_time)
        t0 = time.time()
        with open(run_dir / "flexsim_output.txt", "wb") as out:
            proc = subprocess.Popen(cmd, cwd=str(run_dir), stdout=out, stderr=subprocess.STDOUT)
            last_progress = None
            try:
                while proc.poll() is None:
                    time.sleep(1)
                    reason = should_cancel() if should_cancel else None
                    if reason:
                        raise Cancelled(reason)
                    if time.time() - t0 > limit:
                        raise RuntimeError(f"Simulation did not finish within {limit:.0f} s (see runs/{run_dir.name})")
                    if on_progress and progress.exists():
                        try:
                            mt = float(progress.read_text().strip() or 0)
                        except (OSError, ValueError):
                            continue
                        if mt != last_progress:
                            last_progress = mt
                            on_progress(min(mt / end_time, 1.0) if end_time else 0.0, mt)
            finally:
                if proc.poll() is None:  # 取消、逾時或程式被中斷：把背景的 FlexSim 關掉
                    log.warning("Stopping background FlexSim (pid %s)", proc.pid)
                    proc.kill()
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
        wall = round(time.time() - t0, 1)

        if not result.exists():
            raise RuntimeError(f"FlexSim closed without writing results (see runs/{run_dir.name}). "
                               "Try the same script in FlexSim's Script Console to find the error.")
        values = parse_results(result)
        if "__done" not in values:
            raise RuntimeError(f"FlexSim stopped part-way through writing results (see runs/{run_dir.name})")

        self._cleanup()
        metrics = {m["name"]: values.get(m["name"]) for m in self.cfg["metrics"]}
        return {"metrics": metrics, "model_time": values.get("model_time"), "wall_seconds": wall}

    def _cleanup(self, keep: int = 30) -> None:
        dirs = sorted(d for d in RUNS_DIR.iterdir() if d.is_dir())
        for d in dirs[:-keep]:
            shutil.rmtree(d, ignore_errors=True)


class MockRunner:
    """沒有 FlexSim 時的假模擬，用來測網頁流程。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def run(self, params: dict[str, float], length: float | None,
            should_cancel: Callable[[], str | None] | None = None,
            on_progress: Callable[[float, float], None] | None = None) -> dict[str, Any]:
        end_time = stop_time(self.cfg, params, length)
        steps = int(self.cfg.get("mock_seconds", 3))
        for i in range(steps):
            time.sleep(1)
            reason = should_cancel() if should_cancel else None
            if reason:
                raise Cancelled(reason)
            if on_progress:
                on_progress((i + 1) / steps, end_time * (i + 1) / steps)
        metrics = {m["name"]: round(random.uniform(10, 1000), 2) for m in self.cfg["metrics"]}
        return {"metrics": metrics, "model_time": end_time, "wall_seconds": 3.0}


def make_runner(cfg: dict[str, Any]):
    if cfg.get("mock_mode", False):
        log.warning("MOCK MODE: FlexSim is NOT being called. Set mock_mode=false in config.json.")
        return MockRunner(cfg)
    return FlexSimRunner(cfg)

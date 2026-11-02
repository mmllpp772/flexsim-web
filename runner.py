"""
共用的模擬執行核心（onedrive_worker.py 使用）

- load_config()     讀 config.json
- validate_params() 檢查參數是否在允許範圍內（只接受 config 裡列出的參數）
- make_runner()     依 mock_mode 回傳 FlexSimRunner 或 MockRunner
- public_config()   給網頁看的設定（不含模型路徑、FlexScript、密碼）
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

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
    out = {k: cfg[k] for k in ["title", "subtitle", "run_length", "parameters", "metrics"]}
    out["parameters"] = [{k: v for k, v in p.items() if k != "set_script"} for p in cfg["parameters"]]
    out["metrics"] = [{k: v for k, v in m.items() if k != "script"} for m in cfg["metrics"]]
    out["mock_mode"] = bool(cfg.get("mock_mode"))
    return out


def validate_params(cfg: dict[str, Any], parameters: Any, run_length: Any) -> tuple[dict[str, float], float]:
    """回傳 (乾淨的參數, 模擬長度)；不合法時丟出 ValueError（訊息是英文，直接顯示給使用者）。"""
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

    rl = cfg["run_length"]
    try:
        length = float(run_length if run_length is not None else rl["default"])
    except (TypeError, ValueError):
        raise ValueError(f"{rl['label']} must be a number")
    if math.isnan(length) or length < rl["min"] or length > rl["max"]:
        raise ValueError(f"{rl['label']} must be between {rl['min']} and {rl['max']} {rl['unit']}")
    return clean, length


class FlexSimRunner:
    """包住 FlexSimPy。只能在同一個執行緒裡使用（FlexSim 不支援多執行緒同時操作）。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.controller = None

    def _ensure_started(self) -> None:
        if self.controller is not None:
            return
        import FlexSimPy  # 需要先依 README 安裝好 FlexSimPy 的 .pyd

        fcfg = self.cfg["flexsim"]
        kwargs = dict(fcfg.get("launch_kwargs", {}))
        kwargs.setdefault("programDir", fcfg["program_dir"])
        log.info("Launching FlexSim: %s", kwargs)
        controller = FlexSimPy.launch(**kwargs)
        log.info("Opening model: %s", fcfg["model_path"])
        controller.open(fcfg["model_path"])
        self.controller = controller

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
        limit = float(self.cfg["flexsim"].get("max_run_seconds", 1800))
        while True:  # 某些版本 runToTime 不會等到跑完，這裡保險再等
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
    """沒有 FlexSim 時的假模擬，用排隊公式產生合理的數字，方便測網頁。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def run(self, params: dict[str, float], length_units: float) -> dict[str, Any]:
        time.sleep(random.uniform(2, 4))
        rl = self.cfg["run_length"]
        horizon = length_units * float(rl.get("seconds_per_unit", 1))

        arr = float(params.get("ArrivalInterval", 30))
        proc = float(params.get("ProcessTime", 25))
        servers = max(1, int(params.get("NumOperators", 1)))
        lam, mu = 1 / arr, servers / proc
        rho = min(lam / mu, 0.99)
        throughput = min(lam, mu) * horizon * random.uniform(0.96, 1.02)
        wq = (rho / (1 - rho)) * (proc / servers) * random.uniform(0.85, 1.15)

        values = {
            "throughput": throughput,
            "throughput_per_hour": throughput / (horizon / 3600) if horizon else 0,
            "avg_wait": wq,
            "avg_queue": lam * wq,
            "utilization": rho * 100,
        }
        metrics = {m["name"]: values.get(m["name"], random.uniform(0, 100)) for m in self.cfg["metrics"]}
        return {"metrics": metrics, "model_time": horizon, "wall_seconds": 3.0}


def make_runner(cfg: dict[str, Any]):
    if cfg.get("mock_mode", False):
        log.warning("MOCK MODE: FlexSim is NOT being called. Set mock_mode=false in config.json.")
        return MockRunner(cfg)
    return FlexSimRunner(cfg)

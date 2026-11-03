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

import ctfpyr
from schedule import plan_script, shift_script

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
    out["max_pause_minutes"] = cfg.get("flexsim", {}).get("max_pause_minutes", 60)
    di = cfg.get("daily_input")
    if di:
        out["daily_input"] = {"products": [{k: v for k, v in p.items() if k in ("name", "label", "label_zh")} for p in di["products"]],
                              "defaults": di.get("defaults", {}), "federal_holidays": di.get("federal_holidays", True)}
    out["tables"] = [{k: v for k, v in tb.items() if k in ("name", "label", "label_zh", "key_columns", "view", "product_label", "product_label_zh", "columns")}
                     for tb in cfg.get("tables", [])]
    out["output_views"] = cfg.get("output_views", [])
    if cfg.get("oee"):
        out["oee"] = {k: v for k, v in cfg["oee"].items() if not k.startswith("_")}
    sh = cfg.get("shifts")
    if sh:
        out["shifts"] = {"products": [{k: v for k, v in p.items() if k in ("name", "label", "label_zh")} for p in sh["products"]],
                         "rows": sh["rows"], "columns": sh["columns"], "defaults": sh["defaults"], "max": sh.get("max", 24)}
    ut = cfg.get("utilization")
    if ut:
        out["utilization"] = {
            "groups": [{k: v for k, v in g.items() if k in ("name", "label", "label_zh", "product", "objects")} for g in ut["groups"]],
            "state_names": state_names(cfg),
            "utilized_states": ut.get("utilized_states", [2]),
            "excluded_states": ut.get("excluded_states", [12, 30, 34, 35]),
        }
    di = cfg.get("date_inputs")
    if di:
        out["date_inputs"] = dict(di)
        st = cfg.get("stop") or {}
        if st.get("model_start") and not st.get("set_model_start"):
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


def period_of(cfg: dict[str, Any], params: dict[str, float]) -> tuple[date, date] | None:
    """使用者選的開始日、結束日。"""
    st = cfg.get("stop") or {}
    if not st.get("start_month_param"):
        return None
    y = st["year_param"]
    return (_date_from(params, y, st["start_month_param"], st["start_day_param"], "Start"),
            _date_from(params, y, st["end_month_param"], st["end_day_param"], "End"))


def model_start_for(cfg: dict[str, Any], params: dict[str, float]) -> datetime | None:
    """這次模擬的模型開始時間。
    set_model_start=true：每次都把 FlexSim 的模型開始時間設成使用者選的開始日（不用從固定日期暖機）。
    否則用 config 裡固定的 model_start。"""
    st = cfg.get("stop") or {}
    if st.get("set_model_start") and st.get("start_month_param"):
        start = _date_from(params, st["year_param"], st["start_month_param"], st["start_day_param"], "Start")
        hh, mm = (int(x) for x in str(st.get("start_time_of_day", "00:00")).split(":"))
        return datetime.combine(start, datetime.min.time()).replace(hour=hh, minute=mm)
    if st.get("model_start"):
        return datetime.fromisoformat(st["model_start"])
    return None


def stop_time(cfg: dict[str, Any], params: dict[str, float], length: float | None) -> float:
    """算出要跑到的模型時間（模型時間單位）。"""
    st = cfg.get("stop", {"type": "run_length"})
    unit = float(st.get("time_unit_seconds", 1))
    if st["type"] == "end_of_date":
        model_start = model_start_for(cfg, params)
        y = st["year_param"]
        if st.get("start_month_param"):
            start = _date_from(params, y, st["start_month_param"], st["start_day_param"], "Start")
        else:
            start = None
        end = _date_from(params, y, st["end_month_param"], st["end_day_param"], "End")
        if start and end < start:
            raise ValueError("End date must be on or after the start date")
        # 跑到結束日的隔天、和開始同一個時刻（模型自己的 Apply 是 12:00 開始、跑到結束日隔天 12:00）
        hh, mm = (int(x) for x in str(st.get("start_time_of_day", "00:00")).split(":"))
        end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time()).replace(hour=hh, minute=mm)
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


def table_export_lines(tables: list[dict[str, Any]], path: Path) -> list[str]:
    """把整張 Global Table（含欄名、列名、文字和數字）寫成 tab 分隔的文字檔。"""
    out = [f'fileopen({_fs_str(str(path))}, "w");']
    for i, tb in enumerate(tables, 1):
        v, rr, cc, sv, cv = f"wt{i}", f"wr{i}", f"wc{i}", f"ws{i}", f"wv{i}"
        out += [
            f'Table {v} = Table({_fs_str(tb["table"])});',
            f'fpt("@table\\t{tb["name"]}"); fpr();',
            f'fpt("@cols");',
            f'for (int {cc} = 1; {cc} <= {v}.numCols; {cc}++) {{ fpt("\\t"); fpt({v}.getColHeader({cc})); }}',
            'fpr();',
            f'for (int {rr} = 1; {rr} <= {v}.numRows; {rr}++) {{',
            f'    fpt({v}.getRowHeader({rr}));',
            f'    for (int {cc}b = 1; {cc}b <= {v}.numCols; {cc}b++) {{',
            f'        Variant {cv} = {v}[{rr}][{cc}b];',
            '        fpt("\\t");',
            f'        if ({cv}.type == VAR_TYPE_STRING) {{ string {sv} = {cv}; fpt({sv}); }}',
            f'        else fpt(numtostring({cv}, 0, 6));',
            '    }',
            '    fpr();',
            '}',
        ]
    out += ['fpt("@done"); fpr();', "fileclose();"]
    return out


def parse_tables(path: Path, cfg_tables: list[dict[str, Any]]) -> dict[str, Any]:
    """讀回匯出的表格；略過 skip_rows（例如 TOTAL），數字欄轉成數字。"""
    skip = {tb["name"]: {s.strip().upper() for s in tb.get("skip_rows", [])} for tb in cfg_tables}
    out: dict[str, Any] = {}
    cur = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if parts[0] == "@table":
            cur = {"columns": [], "rows": []}
            out[parts[1]] = cur
        elif parts[0] == "@cols" and cur is not None:
            cur["columns"] = parts[1:]
        elif parts[0] == "@done":
            break
        elif cur is not None and line:
            name = next((k for k, v in out.items() if v is cur), "")
            if parts[0].strip().upper() in skip.get(name, set()):
                continue
            vals: list[Any] = []
            for x in parts[1:]:
                try:
                    vals.append(float(x))
                except ValueError:
                    vals.append(x)
            cur["rows"].append({"row": parts[0], "values": vals})
    return out


# ---------------------------------------------------------------------------
# 設備使用率（各設備在每個狀態的累計時間，對應 FlexSim 的 State Bar / Utilization 圖）
# ---------------------------------------------------------------------------

# FlexSim 內建狀態編號 → 名稱。config.json 的 utilization.state_names 可以覆蓋任何一個。
DEFAULT_STATE_NAMES = {
    1: "Idle", 2: "Processing", 3: "Busy", 4: "Blocked", 5: "Generating", 6: "Empty", 7: "Collecting",
    8: "Releasing", 9: "Waiting for operator", 10: "Waiting for transport", 11: "Breakdown",
    12: "Scheduled down", 13: "Conveying", 14: "Travel empty", 15: "Travel loaded", 16: "Offset travel empty",
    17: "Offset travel loaded", 18: "Loading", 19: "Unloading", 20: "Down", 21: "Setup", 22: "Utilize",
    23: "Full", 24: "Not empty", 25: "Filling", 26: "Starved", 27: "Mixing", 28: "Flowing", 29: "Allocated idle",
    30: "Off shift", 31: "Change over", 32: "Repair", 33: "Maintenance", 34: "Lunch", 35: "On break", 36: "Suspend",
    37: "Available", 38: "Preprocessing", 39: "Postprocessing", 40: "Inspecting", 41: "Operating", 42: "Standby",
    43: "Purging", 44: "Cleaning", 45: "Accelerating", 46: "Maxspeed", 47: "Decelerating", 48: "Stopped",
    49: "Waiting", 50: "Accumulating",
}


def state_names(cfg: dict[str, Any]) -> dict[str, str]:
    ut = cfg.get("utilization") or {}
    n = int(ut.get("max_state", 50))
    names = {str(k): v for k, v in DEFAULT_STATE_NAMES.items() if k <= n}
    for k, v in (ut.get("state_names") or {}).items():
        names[str(int(k))] = str(v)
    return names


def util_objects(cfg: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for g in (cfg.get("utilization") or {}).get("groups", []):
        for o in g.get("objects", []):
            if o not in seen:
                seen.append(o)
    return seen


def util_export_lines(cfg: dict[str, Any], path: Path) -> list[str]:
    """每台設備：@obj 名稱，接著「狀態編號 tab 累計時間」（只寫大於 0 的）。找不到的設備寫 @missing。"""
    ut = cfg["utilization"]
    n = int(ut.get("max_state", 50))
    out = [f'fileopen({_fs_str(str(path))}, "w");', "Object wu;", "double wut;", "int wus;"]
    for name in util_objects(cfg):
        q = _fs_str(name)
        out += [
            f"wu = Model.find({q});",
            f'if (objectexists(wu)) {{ fpt("@obj\\t" + {q}); fpr();',
            f"    for (wus = 1; wus <= {n}; wus++) {{ wut = wu.stats.state().getTotalTimeAt(wus);",
            '        if (wut > 0) { fpt(numtostring(wus, 0, 0) + "\\t" + numtostring(wut, 0, 3)); fpr(); } }',
            f'}} else {{ fpt("@missing\\t" + {q}); fpr(); }}',
        ]
    out += ['fpt("@done"); fpr();', "fileclose();"]
    return out


def parse_util(path: Path) -> dict[str, Any]:
    objects: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    cur: dict[str, float] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if parts[0] == "@obj" and len(parts) > 1:
            cur = objects.setdefault(parts[1], {})
        elif parts[0] == "@missing" and len(parts) > 1:
            missing.append(parts[1]); cur = None
        elif parts[0] == "@done":
            break
        elif cur is not None and len(parts) == 2:
            try:
                cur[str(int(float(parts[0])))] = float(parts[1])
            except ValueError:
                pass
    return {"objects": objects, "missing": missing}


def sync_table_lines(cfg: dict[str, Any]) -> list[str]:
    """Reset 後，把來源表（Bianca 的每日紀錄）的列數、列名和 Date / Weekday 複製到 Strata 的表。
    模型的 On Model Reset 只重建 Bianca 的表，Strata 的表會留著存檔時的舊日期。"""
    sy = cfg.get("sync_tables")
    if not sy or not sy.get("targets"):
        return []
    cols = sy.get("columns", ["Date", "Weekday"])
    cond = " || ".join(f"wyh == {_fs_str(c)}" for c in cols)
    out = ["// ---- copy dates to the other product's record tables ----",
           f"Table wys = Table({_fs_str(sy['source'])});",
           "int wyr; int wyc; int wyk; string wyh;"]
    for i, t in enumerate(sy["targets"], 1):
        v = f"wyt{i}"
        out += [
            f"Table {v} = Table({_fs_str(t)});",
            f"while ({v}.numRows > wys.numRows) {v}.deleteRow({v}.numRows);",
            f"while ({v}.numRows < wys.numRows) {v}.addRow();",
            f"for (wyr = 1; wyr <= wys.numRows; wyr++) {v}.setRowHeader(wyr, wys.getRowHeader(wyr));",
            "for (wyk = 1; wyk <= wys.numCols; wyk++) { wyh = wys.getColHeader(wyk);",
            f"    if ({cond}) {{ for (wyc = 1; wyc <= {v}.numCols; wyc++) {{ if ({v}.getColHeader(wyc) == wyh) {{",
            f"        for (wyr = 2; wyr <= wys.numRows; wyr++) {v}[wyr][wyc] = wys[wyr][wyk]; }} }} }} }}",
        ]
    return out


def build_script(cfg: dict[str, Any], params: dict[str, float], end_time: float, result_path: Path,
                 progress_path: Path | None, tables_path: Path | None = None, plans: dict[str, Any] | None = None,
                 shifts: dict[str, Any] | None = None, util_path: Path | None = None,
                 ct: dict[str, Any] | None = None, ct_defaults: dict[str, Any] | None = None) -> str:
    lines = ["// Generated by flexsim-web. Do not edit."]
    for p in cfg["parameters"]:
        value = params[p["name"]]
        tmpl = p.get("set_script", "Model.parameters." + p["name"] + " = {value};")
        lines.append(tmpl.replace("{value}", repr(value)))
    lines += [
    ]
    st = cfg.get("stop") or {}
    if st.get("set_model_start"):
        ms = model_start_for(cfg, params)
        lines += [
            f'DateTime wr_start = DateTime("{ms:%m/%d/%Y %H:%M:%S}", "%m/%d/%Y %H:%M:%S");',
            'function_s(getmodelunit(START_TIME_NODE), "setDateTime", wr_start);',
        ]
    lines += ctfpyr.script(cfg, ct, ct_defaults)  # 站別 CT 要在 Reset 前寫好（On Model Reset 依 USE 統一機台 CT）
    plan_lines = plan_script(cfg, plans) + shift_script(cfg, shifts)
    lines += plan_lines  # reset 前寫一次（模型 OnReset 可能會讀）
    lines += [
        "resetmodel();",
    ]
    if plan_lines:  # reset 後再寫一次（以防模型 reset 時把表格重建）
        lines += [ln[len("Table "):] if ln.startswith("Table ") else ln for ln in plan_lines]  # 變數已宣告過，第二次不再宣告
    lines += sync_table_lines(cfg)
    lines += ctfpyr.script(cfg, ct, ct_defaults, after_reset=True)
    lines += [
        f"double wr_end = {end_time!r};",
        "double wr_last = -1;",
        "int wr_same = 0;",
        "int wr_n = 0;",
        f"double wr_gap = {86400.0 / float((cfg.get('stop') or {}).get('time_unit_seconds', 1))!r};  // report progress every simulated day",
        "double wr_next = wr_gap;",
    ]
    live = [m for m in cfg["metrics"] if m.get("live", True)]

    def snapshot(indent: str) -> list[str]:
        """把目前的模型時間和指標寫進 progress 檔（最後一行 __end 代表寫完整）。"""
        out = [f"{indent}fileopen({_fs_str(str(progress_path))}, \"w\");",
               f'{indent}fpt("model_time\\t" + numtostring(Model.time, 0, 6)); fpr();']
        for m in live:
            out.append(f'{indent}fpt("{m["name"]}\\t" + numtostring({m["script"]}, 0, 6)); fpr();')
        out += [f'{indent}fpt("__end\\t1"); fpr();', f"{indent}fileclose();"]
        return out

    if progress_path:
        lines += snapshot("")
    lines += [
        "while (Model.time < wr_end) {",
        "    step();",
    ]
    if progress_path:
        lines += [
            "    wr_n++;",
            "    if (wr_n % 20000 == 0 || Model.time >= wr_next) {",
            "        while (wr_next <= Model.time) wr_next += wr_gap;",
            *snapshot("        "),
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
    lines += ['fpt("__done\\t1"); fpr();', "fileclose();"]
    if tables_path and cfg.get("tables"):
        lines += table_export_lines(cfg["tables"], tables_path)
    if util_path and util_objects(cfg):  # 放最後：就算這段出錯，結果和表格也已經寫好了
        lines += util_export_lines(cfg, util_path)
    lines += ["cmdexit();"]
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


def set_suspended(pid: int, suspended: bool) -> None:
    """凍結 / 恢復整個 FlexSim 程式（真正的暫停：停在當下、不吃 CPU，恢復後從原處繼續）。"""
    if os.name == "nt":
        import ctypes
        PROCESS_SUSPEND_RESUME = 0x0800
        k32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
        h = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h:
            raise RuntimeError("Could not access the FlexSim process to pause/resume it")
        try:
            status = (ntdll.NtSuspendProcess if suspended else ntdll.NtResumeProcess)(h)
            if status != 0:
                raise RuntimeError(f"Pause/resume failed (status {status:#x})")
        finally:
            k32.CloseHandle(h)
    else:
        import signal as _signal
        os.kill(pid, _signal.SIGSTOP if suspended else _signal.SIGCONT)


class Cancelled(Exception):
    """工作被取消（網頁按 Cancel，或模擬電腦按 Ctrl+C）。"""


def model_datetime(cfg: dict[str, Any], params: dict[str, float], model_time: float) -> datetime | None:
    st = cfg.get("stop") or {}
    start = model_start_for(cfg, params)
    if not start:
        return None
    return start + timedelta(seconds=model_time * float(st.get("time_unit_seconds", 1)))


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
            on_progress: Callable[[float, float, dict[str, float | None]], None] | None = None,
            want_pause: Callable[[], bool] | None = None,
            on_pause: Callable[[bool], None] | None = None,
            plans: dict[str, Any] | None = None, shifts: dict[str, Any] | None = None,
            ct: dict[str, Any] | None = None, ct_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        end_time = stop_time(self.cfg, params, length)
        run_dir = RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run_dir.mkdir(parents=True)
        script = run_dir / "script.txt"
        result = run_dir / "result.txt"
        progress = run_dir / "progress.txt"
        tables = run_dir / "tables.txt"
        util = run_dir / "utilization.txt"
        want_tables = bool(self.cfg.get("tables"))
        want_util = bool(util_objects(self.cfg))
        script.write_text(build_script(self.cfg, params, end_time, result, progress, tables, plans, shifts, util,
                                       ct, ct_defaults), encoding="utf-8")
        results_done_at: float | None = None
        max_pause = float(self.cfg["flexsim"].get("max_pause_minutes", 60)) * 60

        cmd = [str(self.exe), str(self.model), "/maintenance", "nogui_disablemsg_runscript",
               "/scriptpath", str(script)]
        limit = float(self.cfg["flexsim"].get("max_run_seconds", 1800))
        log.info("Launching FlexSim (end time %.0f)", end_time)
        t0 = time.time()
        with open(run_dir / "flexsim_output.txt", "wb") as out:
            proc = subprocess.Popen(cmd, cwd=str(run_dir), stdout=out, stderr=subprocess.STDOUT)
            last_progress = None
            paused_since: float | None = None
            paused_total = 0.0
            try:
                while proc.poll() is None:
                    time.sleep(1)
                    reason = should_cancel() if should_cancel else None
                    if reason:
                        raise Cancelled(reason)
                    # ----- 暫停 / 繼續 -----
                    pause = bool(want_pause and want_pause())
                    if pause and paused_since is None:
                        set_suspended(proc.pid, True)
                        paused_since = time.time()
                        log.info("FlexSim paused")
                        if on_pause:
                            on_pause(True)
                    elif not pause and paused_since is not None:
                        set_suspended(proc.pid, False)
                        paused_total += time.time() - paused_since
                        paused_since = None
                        log.info("FlexSim resumed")
                        if on_pause:
                            on_pause(False)
                    if paused_since is not None:
                        if time.time() - paused_since > max_pause:
                            raise Cancelled(f"Stopped: paused for more than {max_pause / 60:.0f} minutes.")
                        continue
                    # 結果（和表格）都寫完了，就不必等 FlexSim 自己關閉
                    if results_done_at is None and result.exists() and "__done" in parse_results(result):
                        results_done_at = time.time()
                    if results_done_at is not None:
                        tables_done = not want_tables or (tables.exists() and "@done" in tables.read_text(errors="replace"))
                        util_done = not want_util or (util.exists() and "@done" in util.read_text(errors="replace"))
                        if (tables_done and util_done) or time.time() - results_done_at > 120:
                            time.sleep(2)  # 給 cmdexit 一點時間正常關閉
                            break
                    if time.time() - t0 - paused_total > limit:
                        raise RuntimeError(f"Simulation did not finish within {limit:.0f} s (see runs/{run_dir.name})")
                    if on_progress and progress.exists():
                        try:
                            snap = parse_results(progress)
                        except OSError:
                            continue
                        if "__end" not in snap or snap.get("model_time") is None:
                            continue  # FlexSim 正在寫，下一秒再讀
                        mt = snap["model_time"]
                        if mt != last_progress:
                            last_progress = mt
                            live = {m["name"]: snap.get(m["name"]) for m in self.cfg["metrics"] if m["name"] in snap}
                            on_progress(min(mt / end_time, 1.0) if end_time else 0.0, mt, live)
            finally:
                if proc.poll() is None:  # 取消、逾時或程式被中斷：把背景的 FlexSim 關掉
                    log.warning("Stopping background FlexSim (pid %s)", proc.pid)
                    if paused_since is not None:
                        try:
                            set_suspended(proc.pid, False)
                        except Exception:
                            pass
                    proc.kill()
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
        wall = round(time.time() - t0 - paused_total, 1)

        if not result.exists():
            raise RuntimeError(f"FlexSim closed without writing results (see runs/{run_dir.name}). "
                               "Try the same script in FlexSim's Script Console to find the error.")
        values = parse_results(result)
        if "__done" not in values:
            raise RuntimeError(f"FlexSim stopped part-way through writing results (see runs/{run_dir.name})")

        out_tables = None
        if want_tables:
            if tables.exists() and "@done" in tables.read_text(errors="replace"):
                out_tables = parse_tables(tables, self.cfg["tables"])
            else:
                log.warning("Tables were not exported completely (see runs/%s/tables.txt)", run_dir.name)
        out_util = None
        if want_util:
            if util.exists() and "@done" in util.read_text(errors="replace"):
                out_util = parse_util(util)
                if out_util["missing"]:
                    log.warning("Utilization: objects not found in the model: %s", ", ".join(out_util["missing"]))
            else:
                log.warning("Utilization was not exported completely (see runs/%s/utilization.txt)", run_dir.name)
        self._cleanup()
        metrics = {m["name"]: values.get(m["name"]) for m in self.cfg["metrics"]}
        return {"metrics": metrics, "model_time": values.get("model_time"), "wall_seconds": wall,
                "tables": out_tables, "utilization": out_util}

    def read_tables(self, names: list[str], timeout: float = 600) -> dict[str, Any]:
        """開一次 FlexSim（不跑模擬），把指定的 Global Table 讀出來。用來取得模型目前的 CT / FPYR。"""
        run_dir = RUNS_DIR / ("read-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        run_dir.mkdir(parents=True)
        out_path, script = run_dir / "tables.txt", run_dir / "script.txt"
        defs = [{"name": n, "table": n} for n in names]
        script.write_text("\n".join(table_export_lines(defs, out_path)) + "\ncmdexit();\n", encoding="utf-8")
        cmd = [str(self.exe), str(self.model), "/maintenance", "nogui_disablemsg_runscript", "/scriptpath", str(script)]
        with open(run_dir / "flexsim_output.txt", "wb") as out:
            proc = subprocess.Popen(cmd, cwd=str(run_dir), stdout=out, stderr=subprocess.STDOUT)
            t0 = time.time()
            try:
                while proc.poll() is None:
                    time.sleep(1)
                    if out_path.exists() and "@done" in out_path.read_text(errors="replace"):
                        time.sleep(2)
                        break
                    if time.time() - t0 > timeout:
                        raise RuntimeError("FlexSim did not finish reading the tables")
            finally:
                if proc.poll() is None:
                    proc.kill()
        if not out_path.exists() or "@done" not in out_path.read_text(errors="replace"):
            raise RuntimeError(f"Could not read the tables (see runs/{run_dir.name})")
        return parse_tables(out_path, defs)

    def _cleanup(self, keep: int = 30) -> None:
        dirs = sorted(d for d in RUNS_DIR.iterdir() if d.is_dir())
        for d in dirs[:-keep]:
            shutil.rmtree(d, ignore_errors=True)


class MockRunner:
    """沒有 FlexSim 時的假模擬，用來測網頁流程。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def read_tables(self, names: list[str], timeout: float = 600) -> dict[str, Any]:
        return mock_read_tables(self.cfg, names)

    def run(self, params: dict[str, float], length: float | None,
            should_cancel: Callable[[], str | None] | None = None,
            on_progress: Callable[[float, float, dict[str, float | None]], None] | None = None,
            want_pause: Callable[[], bool] | None = None,
            on_pause: Callable[[bool], None] | None = None,
            plans: dict[str, Any] | None = None, shifts: dict[str, Any] | None = None,
            ct: dict[str, Any] | None = None, ct_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        end_time = stop_time(self.cfg, params, length)
        steps = int(self.cfg.get("mock_seconds", 3))
        paused = False
        i = 0
        while i < steps:
            time.sleep(1)
            reason = should_cancel() if should_cancel else None
            if reason:
                raise Cancelled(reason)
            p = bool(want_pause and want_pause())
            if p != paused:
                paused = p
                if on_pause:
                    on_pause(p)
            if paused:
                continue
            if on_progress:
                f = (i + 1) / steps
                on_progress(f, end_time * f, {m["name"]: round(random.uniform(10, 1000) * f, 2) for m in self.cfg["metrics"]})
            i += 1
        metrics = {m["name"]: round(random.uniform(10, 1000), 2) for m in self.cfg["metrics"]}
        return {"metrics": metrics, "model_time": end_time, "wall_seconds": 3.0, "tables": mock_tables(self.cfg, params),
                "utilization": mock_util(self.cfg, end_time)}


def mock_read_tables(cfg: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """沒有 FlexSim 時的假表格（結構和模型一樣），測網頁用。"""
    out: dict[str, Any] = {}
    cf = cfg.get("ct_fpyr") or {}
    settings = {p["setting_table"] for p in cf.get("products", {}).values()}
    for n in names:
        if n in settings:
            rows = [{"row": st["setting_row"], "values": [1.0 if st["name"] in ("SMT", "DIP") else 0.0, 116.0, 100.0, 0.0, 1.0, 7.0]}
                    for st in cf["stations"]]
            out[n] = {"columns": ["USE", "Cycle Time", "Target Cycle Time", "Weekly improvement percentage", "SHIFT",
                                  "Work Hours Per SHIFT"], "rows": rows}
        else:
            fp = any(st.get("fpyr") and n in st["tables"].values() for st in cf["stations"])
            rows = []
            for i in range(1, 9):
                test = fp and i in (3, 6)
                rows.append({"row": f"{'AOI' if test else 'M'}{i}", "values": [110.0 + i, 98.5 if test else (0.0 if fp else f"Step {i}")]})
            out[n] = {"columns": ["Bianca", "Bianca_FPYR"], "rows": rows}
    return out


def mock_util(cfg: dict[str, Any], end_time: float) -> dict[str, Any] | None:
    objs = util_objects(cfg)
    if not objs:
        return None
    total = end_time * float((cfg.get("stop") or {}).get("time_unit_seconds", 1))
    out = {}
    for o in objs:
        off = total * random.uniform(0.3, 0.42)
        work = total - off
        proc = work * random.uniform(0.55, 0.93)
        rest = work - proc
        wait = rest * random.uniform(0, 0.4)
        blocked = rest * random.uniform(0, 0.3)
        out[o] = {k: round(v, 1) for k, v in {"2": proc, "9": wait, "4": blocked, "1": rest - wait - blocked, "30": off}.items() if v > 0}
    return {"objects": out, "missing": []}


def mock_tables(cfg: dict[str, Any], params: dict[str, float]) -> dict[str, Any] | None:
    if not cfg.get("tables"):
        return None
    start = model_start_for(cfg, params) or datetime(2026, 1, 1)
    end_t = stop_time(cfg, params, None) * float((cfg.get("stop") or {}).get("time_unit_seconds", 1))
    days = max(1, int(round(end_t / 86400)))
    cols = ["Date", "Weekday", "SMT", "DIP", "L1", "L2", "POD", "L4", "PK", "Bonepile"]
    out = {}
    for k, tb in enumerate(t for t in cfg["tables"] if t.get("view") != "oee_source"):
        rows = []
        for d in range(days):
            dt = start + timedelta(days=d)
            off = dt.weekday() == 6
            base = 0 if off else random.randint(380, 520) * (1 - 0.35 * k)
            vals = [dt.strftime("%b ") + str(dt.day), dt.strftime("%a")] + \
                   [0.0 if off else float(int(base * f)) for f in (1.0, 0.97, 0.95, 0.94, 0.9, 0.92, 0.9)] + \
                   [0.0 if off else float(random.randint(0, 12))]
            rows.append({"row": f"DAY{d + 1}", "values": vals})
        out[tb["name"]] = {"columns": cols, "rows": rows}
    # OEE tables: fractions instead of counts
    for tb in cfg["tables"]:
        if tb.get("view") != "oee_source":
            continue
        rows = []
        for d in range(days):
            dt = start + timedelta(days=d)
            off = dt.weekday() == 6
            k = 0.042 if "OEE1" in tb["table"] else 0.075
            rows.append({"row": f"DAY{d + 1}", "values": [dt.strftime("%b ") + str(dt.day), dt.strftime("%a")] +
                         [0.0 if off else round(random.uniform(0.75, 1.0) * k * 10, 4) for _ in tb["columns"][2:]]})
        out[tb["name"]] = {"columns": tb["columns"], "rows": rows}
    return out


def make_runner(cfg: dict[str, Any]):
    if cfg.get("mock_mode", False):
        log.warning("MOCK MODE: FlexSim is NOT being called. Set mock_mode=false in config.json.")
        return MockRunner(cfg)
    return FlexSimRunner(cfg)

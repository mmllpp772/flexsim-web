"""
CT 與 FPYR（輸入參數）
=====================

模型裡有兩層：
  1. 站別設定 WUS_CT_Setting / WUS_CT_Setting_Strata：每站的瓶頸 CT、USE 開關、目標 CT、每週改善 %。
     USE = 1 時，模型在 Reset 會把這一站所有機台的 CT 都改成這個 Cycle Time。
  2. 機台參數 WUS_SMT Parameter、WUS_DIP Parameter、WUS_FA_L1_Parameter …（Strata 各有一張）：
     第 1 欄是每台機台的 CT（秒）；欄名含 FPYR 的那一欄是 FPYR（%）。目前 SMT / DIP 有，
     其他站的參數表日後加上 FPYR 欄，網頁會自動開放。

網頁的預設值直接從模型讀（模型檔有變動才重新讀一次，存在 model_defaults.json），
使用者改過的值隨工作送來，在 Reset 前寫進模型；USE = 0 的站別在 Reset 後再寫一次機台 CT，
避免模型的 On Model Reset 用它保存的舊值蓋掉。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# 只用 USE 和 Cycle Time（Target Cycle Time、Weekly improvement percentage 已不使用）
SETTING_COLS = {"use": "USE", "ct": "Cycle Time"}


def _s(text: str) -> str:
    return '"' + str(text).replace("\\", "/").replace('"', '\\"') + '"'


def tables_to_read(cfg: dict[str, Any]) -> list[str]:
    cf = cfg.get("ct_fpyr")
    if not cf:
        return []
    names: list[str] = []
    for p in cf["products"].values():
        names.append(p["setting_table"])
    for st in cf["stations"]:
        names += list(st["tables"].values())
    return list(dict.fromkeys(names))


def build_defaults(cfg: dict[str, Any], tables: dict[str, Any]) -> dict[str, Any]:
    """把從模型讀到的表格整理成網頁要的預設值（含每格在表裡的列號、欄號）。"""
    cf = cfg["ct_fpyr"]
    out: dict[str, Any] = {}
    for prod, pc in cf["products"].items():
        st_tb = tables.get(pc["setting_table"])
        stations: dict[str, Any] = {}
        machines: dict[str, Any] = {}
        for st in cf["stations"]:
            name = st["name"]
            if st_tb:
                cols = st_tb["columns"]
                for ri, r in enumerate(st_tb["rows"], 1):
                    if r["row"].strip() == st["setting_row"]:
                        rec: dict[str, Any] = {"row": ri}
                        for key, col in SETTING_COLS.items():
                            if col in cols:
                                v = r["values"][cols.index(col)]
                                rec[key] = v if isinstance(v, float) else None
                                rec[key + "_col"] = cols.index(col) + 1
                        stations[name] = rec
                        break
            ptb = tables.get(st["tables"].get(prod, ""))
            if not ptb:
                continue
            cols = ptb["columns"]
            fcol = next((i for i, c in enumerate(cols) if "fpyr" in str(c).lower()), None)  # 0-based
            rows = []
            for ri, r in enumerate(ptb["rows"], 1):
                vals = r["values"]
                if r["row"].startswith("sdt::") or not vals or not isinstance(vals[0], float):
                    continue
                fv = vals[fcol] if fcol is not None and len(vals) > fcol else None
                desc = next((v for i, v in enumerate(vals) if i not in (0, fcol) and isinstance(v, str) and v.strip()), "")
                rows.append({"name": r["row"], "row": ri, "ct": vals[0],
                             "fpyr": fv if isinstance(fv, float) and fv > 0 else None,  # 0 或空白 = 還沒設定
                             "desc": desc})
            machines[name] = rows
            if fcol is not None:
                stations.setdefault(name, {})["fpyr_col"] = fcol + 1
        out[prod] = {"stations": stations, "machines": machines}
    return out


def load_cached(path: Path, model: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        mtime = model.stat().st_mtime
    except OSError:
        return None
    if data.get("model") == str(model) and data.get("mtime") == mtime:
        return data.get("defaults")
    return None


def save_cached(path: Path, model: Path, defaults: dict[str, Any]) -> None:
    path.write_text(json.dumps({"model": str(model), "mtime": model.stat().st_mtime, "defaults": defaults},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def _num(v: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool):
        raise ValueError(f"{what} must be a number")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number")
    if math.isnan(x) or math.isinf(x) or x < lo or x > hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return x


def validate(cfg: dict[str, Any], ct: Any, defaults: dict[str, Any] | None) -> dict[str, Any] | None:
    """只接受模型裡真的有的站別和機台；回傳要寫進模型的值。"""
    if not cfg.get("ct_fpyr") or not defaults or not isinstance(ct, dict):
        return None
    out: dict[str, Any] = {}
    for prod, d in defaults.items():
        p = ct.get(prod)
        if not isinstance(p, dict):
            continue
        clean: dict[str, Any] = {"stations": {}, "machines": {}}
        for name, st in d["stations"].items():
            v = (p.get("stations") or {}).get(name)
            if not isinstance(v, dict):
                continue
            rec = {}
            if "use" in v and st.get("use_col"):
                rec["use"] = 1.0 if v["use"] else 0.0
            if "ct" in v and st.get("ct_col"):
                rec["ct"] = _num(v["ct"], f"{name} CT", 0.1, 100000)
            if rec:
                clean["stations"][name] = rec
        for name, rows in d["machines"].items():
            fpyr_ok = bool(d["stations"].get(name, {}).get("fpyr_col"))
            given = (p.get("machines") or {}).get(name)
            if not isinstance(given, dict):
                continue
            recs = {}
            for m in rows:
                v = given.get(m["name"])
                if not isinstance(v, dict):
                    continue
                rec = {}
                if "ct" in v:
                    rec["ct"] = _num(v["ct"], f"{name} {m['name']} CT", 0.1, 100000)
                if "fpyr" in v and fpyr_ok and m["fpyr"] is not None:  # 只有模型裡本來就有 FPYR 的機台（測試站）
                    rec["fpyr"] = _num(v["fpyr"], f"{name} {m['name']} FPYR", 0, 100)
                if rec:
                    recs[m["name"]] = rec
            if recs:
                clean["machines"][name] = recs
        out[prod] = clean
    return out


def script(cfg: dict[str, Any], clean: dict[str, Any] | None, defaults: dict[str, Any] | None,
           after_reset: bool = False) -> list[str]:
    """Reset 前寫全部；Reset 後只再寫 USE = 0 站別的機台 CT 和 FPYR（USE = 1 的站別由模型統一）。"""
    if not clean or not defaults:
        return []
    cf = cfg["ct_fpyr"]
    lines = ["// ---- CT and FPYR" + (" (after reset)" if after_reset else "") + " ----"]
    for prod, c in clean.items():
        d = defaults[prod]
        pc = cf["products"][prod]
        tables = {st["name"]: st["tables"].get(prod) for st in cf["stations"]}
        if not after_reset:
            for name, rec in c["stations"].items():
                st = d["stations"][name]
                for key, val in rec.items():
                    if key + "_col" not in st:
                        continue
                    lines.append(f'Table({_s(pc["setting_table"])})[{st["row"]}][{st[key + "_col"]}] = {val!r};')
        for name, recs in c["machines"].items():
            st_rec = c["stations"].get(name, {})
            use = st_rec.get("use", d["stations"].get(name, {}).get("use"))
            if after_reset and use:
                continue
            by_name = {m["name"]: m for m in d["machines"][name]}
            for mname, rec in recs.items():
                m = by_name[mname]
                tb = _s(tables[name])
                if "ct" in rec:
                    lines.append(f"Table({tb})[{m['row']}][1] = {rec['ct']!r};")
                if "fpyr" in rec:
                    lines.append(f"Table({tb})[{m['row']}][{d['stations'][name]['fpyr_col']}] = {rec['fpyr']!r};")
    return lines if len(lines) > 1 else []

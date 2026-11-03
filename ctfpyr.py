"""
CT 與 FPYR（輸入參數）
=====================

網頁上填的是每台機台真實的 CT（秒），每台都可以不同：
  - 機台參數 WUS_SMT Parameter、WUS_DIP Parameter、WUS_FA_L1_Parameter …（Strata 各有一張）：
    第 1 欄是機台 CT；欄名含 FPYR 的那一欄是 FPYR（%）。
  - 模型另外有 "<參數表>_User saved"，存使用者自己的機台 CT。站別設定 WUS_CT_Setting 的 USE = 1 時，
    模型在 Reset 會把整站機台都改成同一個 CT，真實值只留在 User saved 裡；所以預設值在 USE = 1 時從 User saved 讀。
  - 網頁送出的模擬一律把 USE 設成 0，並把每台機台 CT 寫進參數表和 User saved，模型就照各機台自己的 CT 跑。
  - CT 變異 WUS_CT_Distribution（各站一列，Bianca / Strata 共用）：0 = 固定、1 = 均勻 ±5%、2 = 三角 ±5%，
    模型的加工時間程式（SMT_CT_Distribution 等）依這個值抽每一件的加工時間。

網頁的預設值直接從模型讀（模型檔有變動才重新讀一次，存在 model_defaults.json）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# 只用 USE 和 Cycle Time（Target Cycle Time、Weekly improvement percentage 已不使用）
SETTING_COLS = {"use": "USE", "ct": "Cycle Time"}
SAVED = "_User saved"
DIST_CHOICES = (0, 1, 2)  # 固定 / 均勻 ±5% / 三角 ±5%


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
        names += [t + SAVED for t in st["tables"].values()]
    if cf.get("distribution_table"):
        names.append(cf["distribution_table"])
    names += param_tables(cfg)
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
            # USE = 1 時參數表被模型統一成同一個 CT，真實的機台 CT 在 User saved
            stb = tables.get(st["tables"][prod] + SAVED)
            saved = {}
            if stb:
                for ri, r in enumerate(stb["rows"], 1):
                    if r["values"] and isinstance(r["values"][0], float):
                        saved[r["row"]] = (ri, r["values"][0])
            use_saved = bool(saved) and bool((stations.get(name) or {}).get("use"))
            cols = ptb["columns"]
            fcol = next((i for i, c in enumerate(cols) if "fpyr" in str(c).lower()), None)  # 0-based
            rows = []
            for ri, r in enumerate(ptb["rows"], 1):
                vals = r["values"]
                if r["row"].startswith("sdt::") or not vals or not isinstance(vals[0], float):
                    continue
                fv = vals[fcol] if fcol is not None and len(vals) > fcol else None
                desc = next((v for i, v in enumerate(vals) if i not in (0, fcol) and isinstance(v, str) and v.strip()), "")
                sv = saved.get(r["row"])
                rows.append({"name": r["row"], "row": ri, "ct": sv[1] if use_saved and sv else vals[0],
                             "saved_row": sv[0] if sv else None,
                             "fpyr": fv if isinstance(fv, float) and fv > 0 else None,  # 0 或空白 = 還沒設定
                             "desc": desc})
            machines[name] = rows
            if stb:
                stations.setdefault(name, {})["saved_table"] = st["tables"][prod] + SAVED
            if fcol is not None:
                stations.setdefault(name, {})["fpyr_col"] = fcol + 1
        out[prod] = {"stations": stations, "machines": machines}
    return out


def build_dist_defaults(cfg: dict[str, Any], tables: dict[str, Any]) -> dict[str, Any]:
    """CT 變異（各站一個值，Bianca / Strata 共用）：{站別: {"row": 列號, "value": 0|1|2}}"""
    cf = cfg.get("ct_fpyr") or {}
    tb = tables.get(cf.get("distribution_table", ""))
    if not tb:
        return {}
    out: dict[str, Any] = {}
    for st in cf["stations"]:
        for ri, r in enumerate(tb["rows"], 1):
            if st.get("dist_row") and r["row"].strip() == st["dist_row"] and r["values"] and isinstance(r["values"][0], float):
                v = int(r["values"][0])
                out[st["name"]] = {"row": ri, "value": v if v in DIST_CHOICES else 0}
    return out


def validate_dist(given: Any, defaults: dict[str, Any] | None) -> dict[str, int] | None:
    if not defaults or not isinstance(given, dict):
        return None
    out = {}
    for name, v in given.items():
        if name in defaults and not isinstance(v, bool) and v in DIST_CHOICES:
            out[name] = int(v)
    return out or None


def dist_script(cfg: dict[str, Any], clean: dict[str, int] | None, defaults: dict[str, Any] | None) -> list[str]:
    if not clean or not defaults:
        return []
    tb = _s(cfg["ct_fpyr"]["distribution_table"])
    return ["// ---- CT variation ----"] + [f"Table({tb})[{defaults[n]['row']}][1] = {v};" for n, v in clean.items()]


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
    if not cfg.get("ct_fpyr") or not defaults:
        return None
    if not isinstance(ct, dict):
        ct = {}
    only = {st["name"] for st in cfg["ct_fpyr"]["stations"] if st.get("bottleneck_only")}
    out: dict[str, Any] = {}
    for prod, d in defaults.items():
        p = ct.get(prod)
        if not isinstance(p, dict):
            p = {}
        clean: dict[str, Any] = {"stations": {}, "machines": {}}
        for name, st in d["stations"].items():
            v = (p.get("stations") or {}).get(name)
            if not isinstance(v, dict) or name not in only:  # 只有沒有機台表的站別（POD）填站別 CT
                continue
            rec = {}
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
                    rec["ct"] = _num(v["ct"], f"{name} {m['name']} CT", 0, 100000)
                if "fpyr" in v and fpyr_ok and m["fpyr"] is not None:  # 只有模型裡本來就有 FPYR 的機台（測試站）
                    rec["fpyr"] = _num(v["fpyr"], f"{name} {m['name']} FPYR", 0, 100)
                if rec:
                    recs[m["name"]] = rec
            if recs:
                clean["machines"][name] = recs
        if clean["stations"] or clean["machines"]:
            out[prod] = clean
    return out or None


def script(cfg: dict[str, Any], clean: dict[str, Any] | None, defaults: dict[str, Any] | None,
           after_reset: bool = False) -> list[str]:
    """每台機台都用自己的 CT：USE 設 0，所有機台 CT（改過的用新值，其餘用預設值）寫進參數表和 User saved。
    Reset 後再寫一次參數表，以防模型 Reset 時改回去。"""
    if not defaults or not cfg.get("ct_fpyr"):
        return []
    cf = cfg["ct_fpyr"]
    clean = clean or {}
    only = {st["name"] for st in cf["stations"] if st.get("bottleneck_only")}
    lines = ["// ---- machine CT and FPYR" + (" (after reset)" if after_reset else "") + " ----"]
    for prod, d in defaults.items():
        c = clean.get(prod) or {"stations": {}, "machines": {}}
        pc = cf["products"][prod]
        tables = {st["name"]: st["tables"].get(prod) for st in cf["stations"]}
        if not after_reset:
            for name, st in d["stations"].items():
                if name in only:
                    rec = c["stations"].get(name, {})
                    if "ct" in rec and st.get("ct_col"):
                        lines.append(f'Table({_s(pc["setting_table"])})[{st["row"]}][{st["ct_col"]}] = {rec["ct"]!r};')
                elif st.get("use_col") and d["machines"].get(name):
                    lines.append(f'Table({_s(pc["setting_table"])})[{st["row"]}][{st["use_col"]}] = 0;')
        for name, rows in d["machines"].items():
            if not rows or not tables.get(name):
                continue
            st = d["stations"].get(name, {})
            given = c["machines"].get(name, {})
            tb, sv = _s(tables[name]), st.get("saved_table")
            for m in rows:
                rec = given.get(m["name"], {})
                ct = rec.get("ct", m["ct"])
                lines.append(f"Table({tb})[{m['row']}][1] = {ct!r};")
                if not after_reset and sv and m.get("saved_row"):
                    lines.append(f"Table({_s(sv)})[{m['saved_row']}][1] = {ct!r};")
                if "fpyr" in rec and st.get("fpyr_col"):
                    lines.append(f"Table({tb})[{m['row']}][{st['fpyr_col']}] = {rec['fpyr']!r};")
                    if not after_reset and sv and m.get("saved_row"):
                        lines.append(f"Table({_s(sv)})[{m['saved_row']}][{st['fpyr_col']}] = {rec['fpyr']!r};")
    return lines if len(lines) > 1 else []


# ---------------------------------------------------------------------------
# 其他參數表（例如 POD）：表裡的數字格都可以改，文字格（例如分配公式）只顯示
# ---------------------------------------------------------------------------

def param_tables(cfg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for grp in cfg.get("param_tables", []):
        names += grp["tables"]
    return list(dict.fromkeys(names))


def build_param_defaults(cfg: dict[str, Any], tables: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for grp in cfg.get("param_tables", []):
        g: dict[str, Any] = {}
        for name in grp["tables"]:
            tb = tables.get(name)
            if not tb:
                continue
            rows = []
            for ri, r in enumerate(tb["rows"], 1):
                if r["row"].startswith("sdt::"):
                    continue
                rows.append({"name": r["row"], "row": ri,
                             "cells": [v if isinstance(v, float) else (str(v) if v not in ("", None) else None) for v in r["values"]]})
            g[name] = {"columns": tb["columns"], "rows": rows}
        out[grp["name"]] = g
    return out


def validate_params_tables(cfg: dict[str, Any], given: Any, defaults: dict[str, Any] | None) -> dict[str, Any] | None:
    """只接受模型裡本來就是數字的格子：{群組: {表: {"列號,欄號": 值}}}"""
    if not defaults or not isinstance(given, dict):
        return None
    out: dict[str, Any] = {}
    groups = {grp["name"]: grp for grp in cfg.get("param_tables", [])}
    for gname, tbs in defaults.items():
        g = given.get(gname)
        if not isinstance(g, dict):
            continue
        for tname, tb in tbs.items():
            cells = g.get(tname)
            if not isinstance(cells, dict):
                continue
            # 有指定 rows 的表：只接受網頁上顯示的那幾列的第 1 欄
            spec = (groups.get(gname, {}).get("rows") or {}).get(tname)
            allowed = {r["name"] for r in tb["rows"] if any(sp["row"] == r["name"] for sp in spec)} if spec else None
            ok = {}
            for key, v in cells.items():
                try:
                    r, c = (int(x) for x in str(key).split(","))
                except ValueError:
                    continue
                row = next((x for x in tb["rows"] if x["row"] == r), None)
                if not row or not (1 <= c <= len(row["cells"])) or not isinstance(row["cells"][c - 1], float):
                    continue
                if allowed is not None and (row["name"] not in allowed or c != 1):
                    continue
                ok[f"{r},{c}"] = _num(v, f"{tname} {row['name']}", -1e9, 1e9)
            if ok:
                out.setdefault(gname, {})[tname] = ok
    return out or None


def params_script(clean: dict[str, Any] | None) -> list[str]:
    if not clean:
        return []
    lines = ["// ---- parameter tables ----"]
    for tbs in clean.values():
        for tname, cells in tbs.items():
            for key, v in cells.items():
                r, c = key.split(",")
                lines.append(f"Table({_s(tname)})[{int(r)}][{int(c)}] = {v!r};")
    return lines

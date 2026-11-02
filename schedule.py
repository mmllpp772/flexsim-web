"""
每日投產計畫（WUS_schedule / WUS_schedule_Strata）
================================================

網頁送來：每個產品的 Target、週六／週日是否休息、每天填的 SMT Input。
這裡負責：
  - 依開始／結束日期產生每天一列（Month、Week of Month、Day of Month、Weekday）
  - 判斷假日（週末規則 + 美國聯邦假日），假日 Holiday = "Y"、投入量 = 0
  - 算 Suggest Input：有填投入量的日子 = 投入量；
    其餘工作日平均分攤 (Target − 已填總和)
  - 產生寫入 FlexSim 的 FlexScript

網頁（index.html）也用同一套規則即時計算給使用者看；送到這裡後會再算一次，以這裡的結果為準。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# 美國聯邦假日（含補假：遇週六提前到週五、遇週日延到週一）
# ---------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def us_federal_holidays(year: int) -> dict[date, str]:
    MON, THU = 0, 3
    days = {
        _observed(date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, MON, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, MON, 3): "Washington's Birthday",
        _last_weekday(year, 5, MON): "Memorial Day",
        _observed(date(year, 6, 19)): "Juneteenth",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, MON, 1): "Labor Day",
        _nth_weekday(year, 10, MON, 2): "Columbus Day",
        _observed(date(year, 11, 11)): "Veterans Day",
        _nth_weekday(year, 11, THU, 4): "Thanksgiving Day",
        _observed(date(year, 12, 25)): "Christmas Day",
    }
    # 隔年元旦若補假到今年 12/31，也算今年的假日
    nxt = _observed(date(year + 1, 1, 1))
    if nxt.year == year:
        days[nxt] = "New Year's Day"
    return days


# ---------------------------------------------------------------------------
# 計畫計算
# ---------------------------------------------------------------------------

def _num(x: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(x, bool):
        raise ValueError(f"{what} must be a number")
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number")
    if math.isnan(v) or math.isinf(v) or v < lo or v > hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return v


def build_plan(start: date, end: date, product: dict[str, Any], defaults: dict[str, Any],
               federal: bool = True) -> dict[str, Any]:
    """依網頁送來的單一產品設定，算出每天的列。product: {target, sat_off, sun_off, inputs: {"YYYY-MM-DD": n}}"""
    target = _num(product.get("target", defaults.get("target", 0)), "Target", 0, 1e9)
    sat_off = bool(product.get("sat_off", defaults.get("sat_off", False)))
    sun_off = bool(product.get("sun_off", defaults.get("sun_off", True)))
    raw = product.get("inputs") or {}
    if not isinstance(raw, dict):
        raise ValueError("Daily inputs are missing")
    hol = {}
    if federal:
        for y in range(start.year, end.year + 1):
            hol.update(us_federal_holidays(y))

    rows = []
    d = start
    while d <= end:
        wd = d.weekday()
        reason = hol.get(d) or ("Saturday" if wd == 5 and sat_off else "Sunday" if wd == 6 and sun_off else None)
        val = raw.get(d.isoformat())
        inp = 0.0 if reason or val in (None, "") else _num(val, f"SMT Input on {d.isoformat()}", 0, 1e9)
        rows.append({"date": d.isoformat(), "month": MONTHS[d.month - 1], "week": math.ceil(d.day / 7),
                     "day": d.day, "weekday": WEEKDAYS[wd], "input": inp, "holiday": reason})
        d += timedelta(days=1)
        if len(rows) > 400:
            raise ValueError("The period is too long (more than 400 days)")

    filled = sum(r["input"] for r in rows)
    open_days = [r for r in rows if not r["holiday"] and r["input"] <= 0]
    per_day = (target - filled) / len(open_days) if open_days else 0.0
    for r in rows:
        if r["holiday"]:
            r["suggest"] = 0.0
        elif r["input"] > 0:
            r["suggest"] = r["input"]
        else:
            r["suggest"] = round(per_day, 2)
    work_days = sum(1 for r in rows if not r["holiday"])
    return {"target": target, "sat_off": sat_off, "sun_off": sun_off, "rows": rows,
            "total_day": len(rows), "total_work_day": work_days, "filled": filled, "per_day": round(per_day, 2)}


def validate_plans(cfg: dict[str, Any], plans: Any, start: date, end: date) -> dict[str, Any] | None:
    di = cfg.get("daily_input")
    if not di:
        return None
    if not isinstance(plans, dict):
        plans = {}
    out = {}
    for p in di["products"]:
        out[p["name"]] = build_plan(start, end, plans.get(p["name"]) or {}, di.get("defaults", {}),
                                    di.get("federal_holidays", True))
    return out


# ---------------------------------------------------------------------------
# 寫入 FlexSim 的 FlexScript
# ---------------------------------------------------------------------------

def _s(text: str) -> str:
    return '"' + str(text).replace("\\", "/").replace('"', '\\"') + '"'


def plan_script(cfg: dict[str, Any], plans: dict[str, Any] | None) -> list[str]:
    if not plans:
        return []
    di = cfg["daily_input"]
    yt, rows_cfg = di["year_table"], di["year_rows"]
    lines = ["// ---- daily input plan ----"]
    for i, p in enumerate(di["products"], 1):
        plan = plans[p["name"]]
        col = int(p["year_table_col"])
        y = f"wy{i}"
        lines.append(f"Table {y} = Table({_s(yt)});")
        for key, val in (("saturday", int(plan["sat_off"])), ("sunday", int(plan["sun_off"])), ("target", plan["target"]),
                         ("total_day", plan["total_day"]), ("total_work_day", plan["total_work_day"])):
            if rows_cfg.get(key):
                lines.append(f"{y}[{int(rows_cfg[key])}][{col}] = {val!r};")
        t = f"wsc{i}"
        n = len(plan["rows"])
        lines.append(f"Table {t} = Table({_s(p['schedule_table'])});")
        lines.append(f"{t}.setSize({n}, Math.max(7, {t}.numCols));")
        for r_i, r in enumerate(plan["rows"], 1):
            lines.append(
                f"{t}[{r_i}][1] = {_s(r['month'])}; {t}[{r_i}][2] = {r['week']}; {t}[{r_i}][3] = {r['day']}; "
                f"{t}[{r_i}][4] = {_s(r['weekday'])}; {t}[{r_i}][5] = {r['input']!r}; {t}[{r_i}][6] = {r['suggest']!r}; "
                f"{t}[{r_i}][7] = {_s('Y' if r['holiday'] else '')};")
    return lines

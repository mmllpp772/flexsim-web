"""
CAD → 3D 廠區佈置圖（在公司電腦上執行，資料不會離開這台電腦）
================================================================

一支程式做完：
  1. DWG 轉 DXF（自動找電腦上的 AutoCAD 或 ODA File Converter）
  2. 讀 CAD：牆壁／隔間、柱子、功能房（封閉框線 + 房名文字）、設備（圖塊或設備圖層上的框）
  3. 設備依名稱對應 Factory_Equipment_3D_Library.html 裡的 3D 模型；庫裡沒有的用內建的 3D 外觀補上
  4. 依名稱或區域判斷設備屬於 Bianca 還是 Strata
  5. 產生 layout3d.html（3D 頁面，可旋轉縮放、可分別隱藏 Bianca / Strata 線體），
     放到 FlexSimShare 共用資料夾，模擬器網頁的「3D 佈置」就看得到

用法（在 flexsim-web 資料夾開命令提示字元）：
    python tools\\build_layout3d.py "Dallas_20261012.dwg" "Factory_Equipment_3D_Library.html"

第一次執行會產生 layout3d_map.json（和 DWG 放在同一個資料夾）：
  程式猜的「哪個圖層是牆、哪個是設備、哪個圖塊是哪台設備、屬於哪條線」都寫在裡面。
  猜錯的地方直接改這個檔，再執行一次就好，不用拍照給任何人。

只用 Python 內建模組。
"""

from __future__ import annotations

import json
import math
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from cad_inspect import dwg_to_dxf, read_pairs  # noqa: E402

UNIT_TO_M = {0: None, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}

# 圖層名稱 → 用途（猜測用，map 檔可以改）
LAYER_GUESS = [
    ("ignore", r"DIM|標註|尺寸|HATCH|填充|GRID|軸線|AXIS|TITLE|圖框|FRAME|DEFPOINTS|VIEWPORT|NOTE|說明|LEADER|引線|CENTER|中心線|SYMB|符號|FURN|家具|LIGHT|燈|ELEC|電|PIPE|管|HVAC|空調|SPRINK|消防|CABLE|線槽|TRAY"),
    ("column", r"COL|柱"),
    ("wall", r"WALL|牆|墙|隔間|隔间|PARTITION|PART|間隔|輕隔|PANEL|CLEANROOM|潔淨|洁净|WINDOW|窗|DOOR|門|门|GLASS|玻璃"),
    ("room", r"ROOM|AREA|ZONE|SPACE|區|区|房|室|ROOMNAME"),
    ("equipment", r"EQUIP|EQP|設備|设备|MACH|機台|机台|LINE|產線|产线|SMT|DIP|FATP|ASSY|組裝|TEST|測試|PACK|包裝|STATION|工站|M-"),
]
# 圖塊名稱不是設備的（門、窗、圖框、指北針、人、車位…）
BLOCK_SKIP = r"(?i)door|門|门|window|窗|title|圖框|图框|north|指北|arrow|箭頭|logo|symbol|符號|axis|軸|grid|tag|elev|stair|樓梯|toilet|馬桶|sink|洗手|person|people|人|car|車位|tree|樹|plant|盆栽|^\*[DXT]"
LINES_DEFAULT = {"bianca": ["bianca"], "strata": ["strata", "strara"]}

# 名稱關鍵字 → 設備種類（用來對 3D 庫和挑內建外觀）
KINDS = [
    ("mounter", r"mount|npm|貼片|贴片|置件|chip|place|fuji|yamaha|siplace|panasert|高速機|泛用"),
    ("printer", r"print|gkg|dek|mpm|印刷|錫膏|锡膏|solder ?paste|screen"),
    ("reflow", r"reflow|回焊|回流|爐|炉|oven|cure|烘烤|烤箱"),
    ("aoi", r"aoi|axi|x-?ray|檢測|检测|inspect|camera|光學|光学|vision|3d ?aoi"),
    ("spi", r"spi\b|spi[_\d]|錫膏檢|锡膏检"),
    ("wave", r"wave|波峰|selective|選焊|选焊"),
    ("dispenser", r"dispens|點膠|点胶|glue|underfill|coat|塗佈|涂布|噴塗"),
    ("loader", r"load|上板|下板|上料|下料|feeder|收板|送板|magazine|料匣|stacker|board ?feeder"),
    ("buffer", r"buffer|暫存|暂存|turn|翻板|flip|shuttle|接駁|接驳|transfer|移載|移载"),
    ("conveyor", r"conveyor|輸送|输送|belt|皮帶|roller|滾筒|line ?body|線體|线体|cv\b|cv[_\d]"),
    ("tester", r"ict|fct|\bft\b|ft[_\d]|test|測試|测试|burn|燒機|烧机|run ?in|tester|ate|pod|chamber|老化|aging"),
    ("robot", r"robot|手臂|機械手|机械手|arm|scara|cobot"),
    ("rack", r"rack|shelf|貨架|货架|料架|儲位|储位|asrs|立體倉|立体仓|stocker|tower|櫃|柜|cabinet|locker"),
    ("cart", r"cart|台車|台车|trolley|agv|amr|叉車|叉车|forklift|pallet|棧板|栈板"),
    ("bench", r"bench|table|桌|工作台|work ?station|desk|作業台|作业台|repair|維修|维修|rework|manual|人工|assy|組裝|组装|screw|鎖螺絲|锁螺丝|pack|包裝|包装|label|貼標|贴标|print ?label"),
    ("wrap", r"wrap|纏繞|缠绕|打包|strap|seal|封箱|carton|紙箱|纸箱"),
]
# 房名關鍵字 → 房間內要擺的東西（功能房）
ROOM_KINDS = [
    ("warehouse", r"warehouse|倉|仓|storage|儲|储|stock|物料|material|料區|料区|wh\b|成品|fg\b|receiv|收料|發料|发料|shipping|出貨|出货"),
    ("office", r"office|辦公|办公|admin|行政|manager|主管"),
    ("meeting", r"meeting|會議|会议|conference|訓練|训练|training"),
    ("lab", r"lab|實驗|实验|qa|qc|品保|品管|quality|檢驗|检验|iqc|oqa|fa\b|分析|calibr|校驗|校验"),
    ("server", r"server|機房|机房|it\b|network|電腦|电脑|ups|電氣|电气|配電|配电|electric"),
    ("rest", r"rest|休息|break|pantry|茶水|cafe|餐|canteen|lounge"),
    ("wc", r"toilet|wc\b|廁|厕|洗手|restroom|lavatory|更衣|locker|changing"),
    ("utility", r"compress|空壓|空压|pump|泵|boiler|鍋爐|锅炉|chiller|冰水|utility|動力|动力|機電|机电|mechanical|水|gas|氣|气|n2|氮"),
]


# ---------------------------------------------------------------------------
# DXF 讀取（只取需要的幾何）
# ---------------------------------------------------------------------------

def parse_dxf(path: Path) -> dict:
    """回傳 {"units", "blocks": {名稱: {"base": (x,y), "ents": [...]}}, "ents": [...]}。
    每個實體是 dict：type, layer, 以及幾何欄位。"""
    out = {"units": None, "blocks": {}, "ents": []}
    section = None
    cur_block = None
    ent = None
    pending_var = None
    last_insert = None

    def finish():
        nonlocal ent, last_insert
        if not ent:
            return
        e = build_entity(ent)
        ent = None
        if e is None:
            return
        if e["type"] == "ATTRIB":
            if last_insert is not None:
                last_insert.setdefault("attribs", {})[e.get("tag", "")] = e.get("text", "")
            return
        if e["type"] == "VERTEX":
            if last_poly is not None:
                last_poly["pts"].append(e["p"])
            return
        target = out["ents"] if section == "ENTITIES" else (out["blocks"][cur_block]["ents"] if cur_block is not None else None)
        if target is None:
            return
        target.append(e)
        if e["type"] == "INSERT":
            last_insert = e
        if e["type"] == "POLYLINE":
            set_poly(e)

    last_poly = None

    def set_poly(e):
        nonlocal last_poly
        last_poly = e

    it = read_pairs(path)
    for code, val in it:
        if code == "0":
            finish()
            v = val.strip()
            if v == "SECTION":
                _, name = next(it)
                section = name.strip()
                continue
            if v == "ENDSEC":
                section = None
                continue
            if section == "BLOCKS":
                if v == "BLOCK":
                    ent = {"_t": "BLOCK"}
                    continue
                if v == "ENDBLK":
                    cur_block = None
                    continue
            if v == "SEQEND":
                last_poly = None
                continue
            if section in ("ENTITIES", "BLOCKS"):
                if v not in ("ATTRIB", "VERTEX"):
                    last_insert = None if v != "INSERT" else last_insert
                ent = {"_t": v}
            continue
        if section == "HEADER":
            if code == "9":
                pending_var = val.strip()
            elif pending_var == "$INSUNITS" and code == "70":
                out["units"] = int(val)
            continue
        if ent is None:
            continue
        if ent["_t"] == "BLOCK":
            if code == "2" and "name" not in ent:
                ent["name"] = val.strip()
            elif code in ("10", "20"):
                ent[code] = float(val)
            if "name" in ent and "10" in ent and "20" in ent:
                cur_block = ent["name"]
                out["blocks"][cur_block] = {"base": (ent["10"], ent["20"]), "ents": []}
                ent = None
            continue
        ent.setdefault(code, []).append(val)
    finish()
    return out


def _f(d, code, i=0, default=0.0):
    try:
        return float(d[code][i])
    except (KeyError, IndexError, ValueError):
        return default


def _s(d, code, default=""):
    v = d.get(code)
    return v[0].strip() if v else default


def build_entity(d: dict) -> dict | None:
    t = d["_t"]
    layer = _s(d, "8", "0")
    if t == "LINE":
        return {"type": "LINE", "layer": layer, "pts": [(_f(d, "10"), _f(d, "20")), (_f(d, "11"), _f(d, "21"))], "closed": False}
    if t == "LWPOLYLINE":
        xs, ys = d.get("10", []), d.get("20", [])
        pts = [(float(x), float(y)) for x, y in zip(xs, ys)]
        flags = int(_f(d, "70"))
        return {"type": "PL", "layer": layer, "pts": pts, "closed": bool(flags & 1)}
    if t == "POLYLINE":
        flags = int(_f(d, "70"))
        if flags & (16 | 64):  # 網格 / 多面網格，略過
            return None
        return {"type": "POLYLINE", "layer": layer, "pts": [], "closed": bool(flags & 1)}
    if t == "VERTEX":
        return {"type": "VERTEX", "layer": layer, "p": (_f(d, "10"), _f(d, "20"))}
    if t in ("CIRCLE", "ARC"):
        cx, cy, r = _f(d, "10"), _f(d, "20"), _f(d, "40")
        a0, a1 = (_f(d, "50"), _f(d, "51")) if t == "ARC" else (0.0, 360.0)
        if a1 <= a0:
            a1 += 360
        n = max(6, min(36, int((a1 - a0) / 12)))
        pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]
        return {"type": t, "layer": layer, "pts": pts, "closed": t == "CIRCLE", "r": r, "c": (cx, cy)}
    if t == "INSERT":
        return {"type": "INSERT", "layer": layer, "name": _s(d, "2"), "p": (_f(d, "10"), _f(d, "20")),
                "sx": _f(d, "41", default=1.0) or 1.0, "sy": _f(d, "42", default=1.0) or 1.0, "rot": _f(d, "50")}
    if t in ("TEXT", "MTEXT"):
        text = "".join(d.get("3", [])) + "".join(d.get("1", []))
        text = re.sub(r"\\[A-Za-z][^;\\]*;|\\P|\\~|[{}]", " ", text)
        text = re.sub(r"%%[cdpCDP]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None
        return {"type": "TEXT", "layer": layer, "p": (_f(d, "10"), _f(d, "20")), "text": text, "h": _f(d, "40", default=0.0)}
    if t == "ATTRIB":
        return {"type": "ATTRIB", "layer": layer, "tag": _s(d, "2"), "text": "".join(d.get("1", [])).strip()}
    return None


# ---------------------------------------------------------------------------
# 幾何小工具
# ---------------------------------------------------------------------------

def xform(p, ins, base=(0.0, 0.0)):
    """圖塊座標 → 外層座標"""
    x, y = (p[0] - base[0]) * ins["sx"], (p[1] - base[1]) * ins["sy"]
    a = math.radians(ins["rot"])
    return (ins["p"][0] + x * math.cos(a) - y * math.sin(a), ins["p"][1] + x * math.sin(a) + y * math.cos(a))


def poly_area(pts):
    return abs(sum(pts[i][0] * pts[i - 1][1] - pts[i - 1][0] * pts[i][1] for i in range(len(pts)))) / 2


def point_in_poly(p, pts):
    x, y, inside = p[0], p[1], False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi, xj, yj = pts[i][0], pts[i][1], pts[j][0], pts[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class Blocks:
    def __init__(self, blocks: dict):
        self.blocks = blocks
        self.bbox_cache: dict[str, tuple | None] = {}

    def bbox(self, name: str, depth: int = 0):
        """圖塊在自己座標系（以插入點為原點）裡的外框"""
        if name in self.bbox_cache:
            return self.bbox_cache[name]
        self.bbox_cache[name] = None
        b = self.blocks.get(name)
        if not b or depth > 8:
            return None
        xs, ys = [], []
        for e in b["ents"]:
            if e["type"] == "INSERT":
                sub = self.bbox(e["name"], depth + 1)
                if sub:
                    for p in ((sub[0], sub[1]), (sub[2], sub[1]), (sub[2], sub[3]), (sub[0], sub[3])):
                        q = xform(p, e)
                        xs.append(q[0]); ys.append(q[1])
            elif "pts" in e:
                for p in e["pts"]:
                    xs.append(p[0]); ys.append(p[1])
        if not xs:
            return None
        bx, by = b["base"]
        res = (min(xs) - bx, min(ys) - by, max(xs) - bx, max(ys) - by)
        self.bbox_cache[name] = res
        return res

    def flatten(self, ins: dict, depth: int = 0):
        """把圖塊裡的線段展開到外層座標（牆壁、柱子是用圖塊畫的時候）"""
        b = self.blocks.get(ins["name"])
        if not b or depth > 6:
            return
        for e in b["ents"]:
            if e["type"] == "INSERT":
                inner = dict(e)
                inner["p"] = xform(e["p"], ins, b["base"])
                inner["rot"] = e["rot"] + ins["rot"]
                inner["sx"] = e["sx"] * ins["sx"]
                inner["sy"] = e["sy"] * ins["sy"]
                yield from self.flatten(inner, depth + 1)
            elif "pts" in e:
                yield {**e, "pts": [xform(p, ins, b["base"]) for p in e["pts"]]}


# ---------------------------------------------------------------------------
# map 檔（使用者可以改的設定）
# ---------------------------------------------------------------------------

def guess_layer(name: str) -> str:
    for role, pat in LAYER_GUESS:
        if re.search(pat, name, re.I):
            return role
    return "auto"


def kind_of(*names: str) -> str:
    s = " ".join(n for n in names if n).lower()
    for k, pat in KINDS:
        if re.search(pat, s, re.I):
            return k
    return "generic"


def room_kind(name: str) -> str:
    for k, pat in ROOM_KINDS:
        if re.search(pat, name or "", re.I):
            return k
    return "other"


def load_map(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SystemExit(f"{path.name} 格式有誤（{e}），請修正或刪掉讓程式重新產生。")
    return {}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build(dxf: Path, lib: Path | None, map_path: Path) -> tuple[dict, dict, list[str]]:
    print("  讀取 DXF…")
    cad = parse_dxf(dxf)
    blocks = Blocks(cad["blocks"])
    m = load_map(map_path)
    log: list[str] = []

    # --- 單位 ---
    unit = m.get("units_to_m") or UNIT_TO_M.get(cad["units"])
    if not unit:
        # 沒寫單位：看圖面大小猜（工廠通常幾十到幾百公尺）
        xs = [p[0] for e in cad["ents"] for p in e.get("pts", [])[:1]] or [0, 1]
        span = max(xs) - min(xs)
        unit = 0.001 if span > 5000 else 1.0
    m.setdefault("_說明", "這個檔是 build_layout3d.py 產生的設定。猜錯的地方直接改，再執行一次程式。"
                 "layers 的值可以是 wall（牆/隔間）、column（柱）、room（功能房框線）、equipment（設備）、text（只用文字）、ignore（不要）、auto（讓程式判斷）。"
                 "blocks 裡每個圖塊：use 填 equipment 或 ignore；model 填 3D 庫裡的模型名稱（空白 = 自動找）；line 填 bianca、strata 或空白（自動判斷）；name 是顯示名稱。")
    m["units_to_m"] = unit
    m.setdefault("wall_height_m", 3.0)
    m.setdefault("wall_thickness_m", 0.15)
    m.setdefault("lines", LINES_DEFAULT)
    m.setdefault("line_labels", {"bianca": "Bianca", "strata": "Strata"})
    layers_map = m.setdefault("layers", {})
    blocks_map = m.setdefault("blocks", {})

    ents = cad["ents"]
    layer_names = sorted({e["layer"] for e in ents})
    for ln in layer_names:
        layers_map.setdefault(ln, guess_layer(ln))
    role = lambda ln: layers_map.get(ln, "auto")  # noqa: E731

    texts = [e for e in ents if e["type"] == "TEXT" and role(e["layer"]) != "ignore"]
    S = lambda p: (p[0] * unit, -p[1] * unit)  # noqa: E731  CAD (x, y) → 3D (x, z)，y 翻轉

    # --- 牆、柱 ---
    walls, cols = [], []
    def add_wall_geom(e):
        pts = e["pts"]
        if e["type"] == "CIRCLE" or (e.get("closed") and len(pts) >= 3 and poly_area(pts) * unit * unit < 2.0 and role(e["layer"]) == "column"):
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            cols.append([round(v, 3) for v in (*S(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)), (max(xs) - min(xs)) * unit, (max(ys) - min(ys)) * unit)])
            return
        seq = pts + ([pts[0]] if e.get("closed") and len(pts) > 2 else [])
        for a, b in zip(seq, seq[1:]):
            L = math.dist(a, b) * unit
            if L < 0.05:
                continue
            walls.append([round(v, 3) for v in (*S(a), *S(b))])

    for e in ents:
        r = role(e["layer"])
        if r not in ("wall", "column"):
            continue
        if e["type"] == "INSERT":
            for g in blocks.flatten(e):
                add_wall_geom({**g, "layer": e["layer"]})
        elif "pts" in e and e["type"] != "TEXT":
            add_wall_geom(e)
    log.append(f"牆／隔間線段 {len(walls)}，柱子 {len(cols)}")

    # --- 功能房：room 圖層的封閉框 + 框裡的文字 ---
    rooms = []
    room_polys = [e for e in ents if role(e["layer"]) == "room" and e.get("closed") and len(e["pts"]) >= 3]
    for e in room_polys:
        area = poly_area(e["pts"]) * unit * unit
        if area < 2 or area > 50000:
            continue
        inside = [t for t in texts if point_in_poly(t["p"], e["pts"])]
        inside.sort(key=lambda t: -t.get("h", 0))
        name = inside[0]["text"] if inside else ""
        rooms.append({"name": name, "kind": room_kind(" ".join(t["text"] for t in inside[:3])), "area": round(area, 1),
                      "pts": [[round(v, 3) for v in S(p)] for p in e["pts"]]})
    log.append(f"功能房 {len(rooms)}（有名稱 {sum(1 for r in rooms if r['name'])}）")

    # --- Bianca / Strata 判斷用：含線名的文字所在的封閉區域 ---
    line_kw = {k: [w.lower() for w in v] for k, v in m["lines"].items()}
    def line_of_name(*names):
        s = " ".join(n for n in names if n).lower()
        for k, kws in line_kw.items():
            if any(w and w in s for w in kws):
                return k
        return ""
    closed_all = [e for e in ents if e.get("closed") and len(e["pts"]) >= 3]
    line_zones = []  # (line, poly)
    for t in texts:
        ln = line_of_name(t["text"])
        if not ln:
            continue
        best = None
        for e in closed_all:
            a = poly_area(e["pts"]) * unit * unit
            if 20 <= a <= 60000 and point_in_poly(t["p"], e["pts"]) and (best is None or a < best[0]):
                best = (a, e["pts"])
        line_zones.append((ln, best[1] if best else None, t["p"]))

    # --- 設備 ---
    equip = []
    skip_re = re.compile(BLOCK_SKIP)
    insert_names = Counter(e["name"] for e in ents if e["type"] == "INSERT" and role(e["layer"]) not in ("wall", "column", "ignore"))
    for name, n in insert_names.most_common():
        bb = blocks.bbox(name)
        w = (bb[2] - bb[0]) * unit if bb else 0
        d = (bb[3] - bb[1]) * unit if bb else 0
        default_use = "equipment" if bb and 0.25 <= max(w, d) <= 60 and not skip_re.search(name) else "ignore"
        rec = blocks_map.setdefault(name, {"use": default_use, "model": "", "line": "", "name": "", "_count": n, "_size_m": f"{w:.1f} x {d:.1f}"})
        rec["_count"] = n

    def text_inside(cx, cy, hw, hd, rot):
        a = math.radians(-rot)
        for t in texts:
            dx, dy = t["p"][0] - cx, t["p"][1] - cy
            x, y = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            if abs(x) <= hw and abs(y) <= hd:
                return t["text"]
        return ""

    def assign_line(*names, center=None):
        ln = line_of_name(*names)
        if ln or center is None:
            return ln
        for z_ln, poly, _ in line_zones:
            if poly and point_in_poly(center, poly):
                return z_ln
        # 沒有框：取 40 公尺內最近的線名文字
        best = None
        for z_ln, _, p in line_zones:
            dd = math.dist(p, center) * unit
            if dd <= 40 and (best is None or dd < best[0]):
                best = (dd, z_ln)
        return best[1] if best else ""

    for e in ents:
        if e["type"] != "INSERT" or role(e["layer"]) in ("wall", "column", "ignore"):
            continue
        rec = blocks_map.get(e["name"])
        if not rec or rec.get("use") != "equipment":
            continue
        bb = blocks.bbox(e["name"])
        if not bb:
            continue
        base = cad["blocks"][e["name"]]["base"]
        cx_l, cy_l = (bb[0] + bb[2]) / 2 + base[0], (bb[1] + bb[3]) / 2 + base[1]
        c = xform((cx_l, cy_l), e, base)
        w, d = abs((bb[2] - bb[0]) * e["sx"]), abs((bb[3] - bb[1]) * e["sy"])
        attrs = " ".join(v for v in e.get("attribs", {}).values() if v)
        label = rec.get("name") or attrs or text_inside(c[0], c[1], w / 2, d / 2, e["rot"]) or e["name"]
        line = rec.get("line") or assign_line(e["name"], e["layer"], attrs, label, center=c)
        equip.append({"name": label[:60], "block": e["name"], "x": round(S(c)[0], 3), "z": round(S(c)[1], 3),
                      "w": round(w * unit, 3), "d": round(d * unit, 3), "rot": round(e["rot"], 2),
                      "kind": kind_of(rec.get("model", ""), label, e["name"], e["layer"]), "model": rec.get("model", ""), "line": line})

    # 設備圖層上直接畫的框（沒有做成圖塊的設備）
    for e in ents:
        if role(e["layer"]) != "equipment" or e["type"] not in ("PL", "POLYLINE") or not e.get("closed") or len(e["pts"]) < 4:
            continue
        area = poly_area(e["pts"]) * unit * unit
        if not 0.1 <= area <= 400:
            continue
        pts = e["pts"]
        # 最長邊的方向當作設備方向
        i = max(range(len(pts)), key=lambda k: math.dist(pts[k], pts[(k + 1) % len(pts)]))
        a, b = pts[i], pts[(i + 1) % len(pts)]
        rot = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        ang = math.radians(-rot)
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        loc = [((p[0] - cx) * math.cos(ang) - (p[1] - cy) * math.sin(ang), (p[0] - cx) * math.sin(ang) + (p[1] - cy) * math.cos(ang)) for p in pts]
        w, d = max(p[0] for p in loc) - min(p[0] for p in loc), max(p[1] for p in loc) - min(p[1] for p in loc)
        label = text_inside(cx, cy, w / 2, d / 2, rot) or ""
        equip.append({"name": label[:60] or e["layer"], "block": "", "x": round(S((cx, cy))[0], 3), "z": round(S((cx, cy))[1], 3),
                      "w": round(w * unit, 3), "d": round(d * unit, 3), "rot": round(rot, 2), "kind": kind_of(label, e["layer"]),
                      "model": "", "line": assign_line(e["layer"], label, center=(cx, cy))})

    by_line = Counter(x["line"] or "（未分類）" for x in equip)
    log.append(f"設備 {len(equip)}：" + "，".join(f"{k} {v}" for k, v in by_line.most_common()))
    log.append("設備種類：" + "，".join(f"{k} {v}" for k, v in Counter(x["kind"] for x in equip).most_common()))

    # 房間名稱文字（沒有框的也顯示成地面標籤）
    room_texts = [{"text": t["text"][:40], "x": round(S(t["p"])[0], 2), "z": round(S(t["p"])[1], 2)}
                  for t in texts if role(t["layer"]) == "room" and len(t["text"]) <= 40]

    data = {"unit": unit, "wall_h": m["wall_height_m"], "wall_t": m["wall_thickness_m"], "walls": walls, "cols": cols,
            "rooms": rooms, "room_texts": room_texts[:800], "equip": equip, "lines": m["line_labels"]}
    return data, m, log


# ---------------------------------------------------------------------------
# 3D 庫：把庫的 <script> 帶進來（3D 頁面在瀏覽器裡找出建立設備的函式）
# ---------------------------------------------------------------------------

def library_parts(lib: Path | None) -> tuple[str, str, list[str]]:
    """回傳（庫的 body HTML, 庫的 script 標籤們, 函式名稱清單）"""
    if not lib or not lib.exists():
        return "", "", []
    html = lib.read_text(encoding="utf-8", errors="replace")
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    body_html = body.group(1) if body else ""
    scripts = re.findall(r"<script\b[^>]*>.*?</script>", html, re.S | re.I)
    body_html = re.sub(r"<script\b[^>]*>.*?</script>", "", body_html, flags=re.S | re.I)
    styles = "".join(re.findall(r"<style\b[^>]*>.*?</style>", html, re.S | re.I))
    # 頂層宣告的名稱（const / let 不會掛在 window 上，3D 頁面要用名稱去找）
    names = set(re.findall(r"^\s{0,4}(?:export\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)", html, re.M))
    names |= set(re.findall(r"\bwindow\.([A-Za-z_$][\w$]*)\s*=(?!=)", html))
    names = sorted(names)
    return styles + body_html, "\n".join(scripts), names


def write_html(data: dict, lib: Path | None, out: Path) -> None:
    vendor = ROOT / "vendor"
    three = (vendor / "three.min.js").read_text(encoding="utf-8")
    orbit = (vendor / "three-OrbitControls.js").read_text(encoding="utf-8")
    viewer = (HERE / "layout3d_viewer.js").read_text(encoding="utf-8")
    lib_body, lib_scripts, names = library_parts(lib)
    data = {**data, "libNames": names[:3000], "kinds": [[k, p] for k, p in KINDS]}
    safe = lambda s: s.replace("</script", "<\\/script")  # noqa: E731
    page = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>3D 佈置圖</title>
<script>
// 3D 庫自己的畫面和動畫不要跑（只借用它建立設備的函式）
window.__rafOrig = window.requestAnimationFrame.bind(window);
window.requestAnimationFrame = function () {{ return 0; }};
</script>
</head><body>
<div id="__lib" style="display:none" aria-hidden="true">{lib_body}</div>
{lib_scripts}
<script>
if (!window.THREE || !window.THREE.WebGLRenderer) {{
{safe(three)}
}}
</script>
<script>
{safe(orbit)}
</script>
<script>window.LAYOUT = {json.dumps(data, ensure_ascii=False, separators=(",", ":"))};</script>
<script>
{safe(viewer)}
</script>
</body></html>
"""
    out.write_text(page, encoding="utf-8")


def share_dir() -> Path | None:
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT))
        from onedrive_worker import resolve_share_dir  # noqa: WPS433
        import contextlib, io  # noqa: E401
        with contextlib.redirect_stdout(io.StringIO()):
            p = resolve_share_dir(cfg["onedrive"]["share_dir"])
        return p if p.is_dir() else None
    except (Exception, SystemExit):
        return None


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cad = Path(sys.argv[1]).resolve()
    lib = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    if not cad.exists():
        sys.exit(f"找不到 {cad}")
    map_path = cad.with_name("layout3d_map.json")

    print("1/4 轉檔…")
    dxf = cad if cad.suffix.lower() == ".dxf" else dwg_to_dxf(cad)
    if not dxf:
        sys.exit(1)
    print("2/4 讀 CAD、判斷牆壁／功能房／設備…")
    data, m, log = build(dxf, lib, map_path)
    map_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print("3/4 產生 3D 頁面…")
    out = ROOT / "layout3d.html"
    write_html(data, lib, out)
    dest = None
    sd = share_dir()
    if sd:
        dest = sd / "layout3d.html"
        shutil.copy2(out, dest)
    print("4/4 完成\n")
    for line in log:
        print("  " + line)
    print(f"\n  3D 頁面：{out}")
    if dest:
        print(f"  已放到共用資料夾：{dest}（模擬器網頁 → 指標呈現 →「3D 佈置」）")
    else:
        print("  找不到共用資料夾，請把 layout3d.html 複製到 FlexSimShare（和 Simulator.html 放一起）。")
    print(f"  設定檔：{map_path}")
    print("  牆壁、設備、Bianca / Strata 判斷不對的話，改設定檔再執行一次。")


if __name__ == "__main__":
    main()

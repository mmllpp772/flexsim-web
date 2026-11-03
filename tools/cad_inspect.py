"""
CAD 與 3D 庫盤點（在公司電腦上執行，資料不會離開這台電腦）
==========================================================

做兩件事：
  1. 把 DWG 轉成 DXF（用程式附的 LibreDWG 轉檔程式，不需要 AutoCAD）
  2. 讀 DXF 和 3D 庫 HTML，寫出一份「盤點清單」cad_report.txt：
     只有圖層名稱、圖塊名稱、數量、單位、3D 庫裡的設備名稱，沒有座標也沒有圖面內容。

用法（在 flexsim-web 資料夾裡開命令提示字元）：
    python tools\\cad_inspect.py "Dallas_20261012.dwg" "Factory_Equipment_3D_Library.html"

已經有 DXF 的話，第一個參數直接給 .dxf 就好。
只用 Python 內建模組，不需要另外安裝套件。
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

UNITS = {0: "未指定", 1: "英吋", 2: "英呎", 4: "公釐 (mm)", 5: "公分 (cm)", 6: "公尺 (m)"}


# ---------------------------------------------------------------------------
# 1. DWG → DXF
# ---------------------------------------------------------------------------

def find_oda() -> str | None:
    pats = [r"C:\Program Files\ODA\ODAFileConverter*\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA\ODAFileConverter*\ODAFileConverter.exe"]
    hits = sorted(h for p in pats for h in glob.glob(p))
    return hits[-1] if hits else None


def find_accore() -> str | None:
    pats = [r"C:\Program Files\Autodesk\AutoCAD*\accoreconsole.exe",
            r"C:\Program Files\Autodesk\*\accoreconsole.exe"]
    hits = sorted(h for p in pats for h in glob.glob(p))
    return hits[-1] if hits else None


def find_libredwg() -> str | None:
    """程式附的 LibreDWG（tools\\libredwg\\dwg2dxf.exe），不需要另外安裝任何軟體"""
    exe = Path(__file__).resolve().parent / "libredwg" / "dwg2dxf.exe"
    if exe.exists() and os.name == "nt":
        return str(exe)
    return shutil.which("dwg2dxf")


def libredwg_convert(exe: str, dwg: Path, out: Path) -> bool:
    # 路徑有中文時這個轉檔程式可能讀不到，所以先複製到暫存資料夾（英文路徑）再轉
    tmp = Path(tempfile.mkdtemp(prefix="dwg2dxf_"))
    src, dst = tmp / "in.dwg", tmp / "out.dxf"
    shutil.copy2(dwg, src)
    try:
        r = subprocess.run([exe, "-y", "-o", str(dst), str(src)], timeout=1800, capture_output=True,
                           cwd=str(Path(exe).parent))
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  轉檔程式無法執行：{e}")
        return False
    if dst.exists() and dst.stat().st_size > 1000:
        shutil.move(str(dst), out)
        return True
    msg = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
    print("  轉檔失敗：" + " / ".join(msg))
    return False


def dwg_to_dxf(dwg: Path) -> Path | None:
    out = dwg.with_suffix(".dxf")
    if out.exists() and out.stat().st_mtime >= dwg.stat().st_mtime:
        print(f"  已經有轉好的 DXF：{out.name}")
        return out

    lib = find_libredwg()
    if lib:
        print("  用程式附的 LibreDWG 轉檔（不需要 AutoCAD），大檔案可能要幾分鐘…")
        if libredwg_convert(lib, dwg, out):
            return out
        print("  改試電腦上的其他轉檔軟體。")

    oda = find_oda()
    if oda:
        print(f"  用 ODA File Converter 轉檔：{oda}")
        tmp_in, tmp_out = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        shutil.copy2(dwg, tmp_in / dwg.name)
        # 參數：輸入資料夾 輸出資料夾 版本 格式 遞迴 稽核 [篩選]
        subprocess.run([oda, str(tmp_in), str(tmp_out), "ACAD2018", "DXF", "0", "1", "*.DWG"], timeout=600)
        got = list(tmp_out.glob("*.dxf"))
        if got:
            shutil.move(str(got[0]), out)
            return out
        print("  ODA 轉檔沒有產生檔案，改試 AutoCAD。")

    acc = find_accore()
    if acc:
        print(f"  用 AutoCAD（背景模式）轉檔：{acc}")
        scr = Path(tempfile.mkdtemp()) / "todxf.scr"
        # DXFOUT：檔名 → 版本 2018 → 精度 16
        scr.write_text(f'_.DXFOUT\n"{out}"\n_V\n2018\n16\n_.QUIT\n_Y\n', encoding="utf-8")
        subprocess.run([acc, "/i", str(dwg), "/s", str(scr), "/l", "en-US"], timeout=900,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out.exists():
            return out
        print("  AutoCAD 轉檔沒有產生檔案。")

    print("\n  ✗ 這台電腦找不到可以自動轉檔的程式（AutoCAD 或 ODA File Converter）。")
    print("    請用 AutoCAD 或 DWG TrueView 打開 DWG → 另存新檔 → 檔案類型選「AutoCAD 2018 DXF」，")
    print("    存在同一個資料夾，再執行一次這支程式（第一個參數改成 .dxf）。")
    return None


# ---------------------------------------------------------------------------
# 2. 讀 DXF（ASCII 格式）
# ---------------------------------------------------------------------------

def read_pairs(path: Path):
    raw = path.read_bytes()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise SystemExit("這是二進位 DXF，請另存成一般（ASCII）DXF。")
    for enc in ("utf-8", "cp950", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        yield lines[i].strip(), lines[i + 1].rstrip()


def inspect_dxf(path: Path) -> dict:
    info = {"units": None, "ext": None, "layers": [], "ent": Counter(), "layer_ent": defaultdict(Counter),
            "inserts": Counter(), "insert_layer": defaultdict(Counter), "blocks": Counter(),
            "texts": defaultdict(Counter), "closed_poly_layer": Counter()}
    section = None
    cur_block = None
    ent = None  # 目前讀到的實體：{"type":..., "8": layer, ...}
    pending_var = None
    ext = {}

    def flush():
        nonlocal ent
        if not ent:
            return
        t, layer = ent["type"], ent.get("8", "0")
        if section == "ENTITIES":
            info["ent"][t] += 1
            info["layer_ent"][layer][t] += 1
            if t == "INSERT":
                info["inserts"][ent.get("2", "?")] += 1
                info["insert_layer"][ent.get("2", "?")][layer] += 1
            if t in ("TEXT", "MTEXT") and ent.get("1"):
                s = re.sub(r"\\[A-Za-z][^;]*;|[{}]|\\P", " ", ent["1"]).strip()
                if s:
                    info["texts"][layer][s[:40]] += 1
            if t == "LWPOLYLINE" and int(ent.get("70", "0") or 0) & 1:
                info["closed_poly_layer"][layer] += 1
        elif section == "BLOCKS" and cur_block is not None:
            info["blocks"][cur_block] += 1
        ent = None

    it = read_pairs(path)
    for code, val in it:
        if code == "0":
            flush()
            if val == "SECTION":
                code2, name = next(it)
                section = name.strip()
                continue
            if val == "ENDSEC":
                section = None
                continue
            if section == "BLOCKS" and val == "BLOCK":
                cur_block = ""
                ent = {"type": "BLOCKHDR"}
                continue
            if section == "BLOCKS" and val == "ENDBLK":
                cur_block = None
                continue
            if section == "TABLES" and val == "LAYER":
                ent = {"type": "LAYERREC"}
                continue
            if section in ("ENTITIES", "BLOCKS"):
                ent = {"type": val}
            continue
        if section == "HEADER":
            if code == "9":
                pending_var = val.strip()
            elif pending_var == "$INSUNITS" and code == "70":
                info["units"] = int(val)
            elif pending_var in ("$EXTMIN", "$EXTMAX") and code in ("10", "20"):
                ext[(pending_var, code)] = float(val)
            continue
        if ent is None:
            continue
        if ent["type"] == "LAYERREC":
            if code == "2":
                info["layers"].append(val.strip())
                ent = None
            continue
        if ent["type"] == "BLOCKHDR":
            if code == "2":
                cur_block = val.strip()
                info["blocks"][cur_block] += 0
                ent = None
            continue
        if code in ("8", "2", "70") and code not in ent:
            ent[code] = val.strip()
        elif code == "1":
            ent["1"] = ent.get("1", "") + val
        elif code == "3" and ent["type"] == "MTEXT":
            ent["1"] = ent.get("1", "") + val
    flush()
    if len(ext) == 4:
        info["ext"] = (ext[("$EXTMAX", "10")] - ext[("$EXTMIN", "10")], ext[("$EXTMAX", "20")] - ext[("$EXTMIN", "20")])
    return info


# ---------------------------------------------------------------------------
# 3. 讀 3D 庫 HTML（只看結構：用了什麼程式庫、有哪些設備名稱、建立設備的函式）
# ---------------------------------------------------------------------------

def inspect_library(path: Path) -> list[str]:
    html = path.read_text(encoding="utf-8", errors="replace")
    out = [f"檔案大小：{len(html) / 1e6:.1f} MB"]
    t = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if t:
        out.append(f"標題：{t.group(1).strip()[:80]}")
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)', html, re.I)
    out.append("外部程式：" + (", ".join(s[-70:] for s in srcs[:10]) or "（沒有，全部內嵌）"))
    imports = sorted(set(re.findall(r'(?:from|import)\s*["\']([^"\']+)["\']', html)))
    if imports:
        out.append("import：" + ", ".join(i[-70:] for i in imports[:15]))
    has_three = re.search(r"THREE\.|three(\.module)?(\.min)?\.js", html)
    n_data = len(re.findall(r"data:[a-z/+.-]+;base64,", html))
    n_model = len(re.findall(r"(?i)\.(glb|gltf|obj|fbx)\b|GLTFLoader|OBJLoader|FBXLoader", html))
    out.append(f"three.js：{'有' if has_three else '沒看到'}")
    out.append(f"內嵌資料（data: 開頭，通常是 3D 模型或貼圖）：{n_data} 個")
    out.append(f"glTF / GLB / OBJ / FBX 字樣：{n_model} 處")
    funcs = Counter(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", html))
    funcs.update(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", html))
    make = [f for f in funcs if re.match(r"(?i)(make|create|build|add|new|gen)", f)]
    out.append(f"建立物件的函式（{len(make)} 個）：" + ", ".join(sorted(make)[:80]))
    names = Counter()
    for k, v in re.findall(r'\b(name|label|title|type|id|key|category)\s*:\s*["\']([^"\'\n]{2,40})["\']', html):
        names[(k, v)] += 1
    by_key = defaultdict(list)
    for (k, v), _ in names.most_common():
        by_key[k].append(v)
    for k, vs in by_key.items():
        out.append(f"{k}: 共 {len(vs)} 個 → " + " | ".join(vs[:60]))
    # 設備清單常見寫法：一個大物件 { 'SMT': ..., 'Reflow': ... } 或陣列
    keys = re.findall(r'^\s*["\']?([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff \-]{1,30})["\']?\s*:\s*(?:\{|function|\()', html, re.M)
    if keys:
        out.append(f"物件鍵（可能是設備清單，{len(set(keys))} 個）：" + " | ".join(list(dict.fromkeys(keys))[:80]))
    return out


# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cad = Path(sys.argv[1]).resolve()
    lib = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    report = [f"CAD 與 3D 庫盤點　（只有名稱與數量，沒有座標）", ""]

    print("1/3 轉檔…")
    dxf = cad if cad.suffix.lower() == ".dxf" else dwg_to_dxf(cad)

    if dxf:
        print("2/3 讀 DXF…")
        d = inspect_dxf(dxf)
        report.append("== CAD ==")
        report.append(f"單位：{UNITS.get(d['units'], d['units'])}")
        if d["ext"]:
            report.append(f"圖面範圍：約 {d['ext'][0]:,.0f} × {d['ext'][1]:,.0f}（圖面單位）")
        report.append("實體種類：" + ", ".join(f"{k} {v}" for k, v in d["ent"].most_common()))
        report.append("")
        report.append(f"-- 圖層（{len(d['layer_ent'])} 個有東西）：名稱：數量（主要種類） --")
        for layer, c in sorted(d["layer_ent"].items(), key=lambda x: -sum(x[1].values())):
            top = ", ".join(f"{k} {v}" for k, v in c.most_common(3))
            closed = d["closed_poly_layer"].get(layer)
            report.append(f"  {layer}：{sum(c.values())}（{top}{f'，封閉多段線 {closed}' if closed else ''}）")
        report.append("")
        report.append(f"-- 擺放的圖塊（{len(d['inserts'])} 種）：名稱 × 次數 @ 圖層 --")
        for name, n in d["inserts"].most_common():
            layers = ", ".join(l for l, _ in d["insert_layer"][name].most_common(2))
            report.append(f"  {name} × {n} @ {layers}")
        report.append("")
        report.append("-- 文字（每個圖層最常出現的前 8 個，通常是房間或設備名稱） --")
        for layer, c in sorted(d["texts"].items(), key=lambda x: -sum(x[1].values()))[:25]:
            report.append(f"  {layer}：" + " | ".join(s for s, _ in c.most_common(8)))
        report.append("")

    if lib and lib.exists():
        print("3/3 讀 3D 庫…")
        report.append("== 3D 庫 ==")
        report += ["  " + x for x in inspect_library(lib)]

    out = Path("cad_report.txt").resolve()
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\n完成：{out}")
    if dxf and dxf != cad:
        print(f"DXF：{dxf}")
    print("請打開 cad_report.txt 看過內容，沒問題再拍照或複製給我。")


if __name__ == "__main__":
    main()

"""
從 FlexSim 儀表板匯出的 CSV，自動產生「設備使用率」的設備清單
==============================================================

用法：
  1. 在 FlexSim 的使用率圖上按右鍵 → Export to CSV，存到這個資料夾裡的 util_csv 資料夾。
     檔名就是網頁上顯示的群組名稱，例如：
        SMT.csv   DIP.csv   L1.csv   L2.csv   L4.csv   POD.csv   Packing.csv
        SMT_Strata.csv   DIP_Strata.csv   ...（檔名含 Strata 的歸到 Strata，其餘歸到 Bianca）
  2. 執行：python import_util_groups.py
  3. 重新啟動 onedrive_worker.py

CSV 欄位是 FlexSim 匯出的 Object, State, Time, Utilization；這裡只讀 Object 欄，依出現順序排列。
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV_DIR = HERE / "util_csv"
CONFIG = HERE / "config.json"
ORDER = ["SMT", "DIP", "L1", "L2", "L4", "POD", "PACKING", "PK"]


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp950", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def objects_in(path: Path) -> list[str]:
    rows = list(csv.reader(io.StringIO(read_text(path))))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    col = head.index("object") if "object" in head else 0
    seen: list[str] = []
    for r in rows[1:]:
        if len(r) > col:
            name = r[col].strip()
            if name and name not in seen:
                seen.append(name)
    return seen


def main() -> None:
    if not CSV_DIR.is_dir():
        CSV_DIR.mkdir()
        sys.exit(f"已建立 {CSV_DIR}。請把 FlexSim 匯出的 CSV 放進去，再執行一次。")
    files = sorted(CSV_DIR.glob("*.csv"))
    if not files:
        sys.exit(f"{CSV_DIR} 裡沒有 CSV 檔。")

    groups = []
    for f in files:
        objs = objects_in(f)
        if not objs:
            print(f"  ! {f.name}: 讀不到設備名稱，略過")
            continue
        stem = f.stem
        is_strata = "strata" in stem.lower()
        label = stem.replace("_Strata", "").replace("_strata", "").replace(" Strata", "").strip(" _")
        groups.append({
            "name": stem.lower().replace(" ", "_"),
            "label": label, "label_zh": label,
            "product": "strata" if is_strata else "bianca",
            "objects": objs,
        })
        print(f"  ✓ {f.name}: {len(objs)} 台（{'Strata' if is_strata else 'Bianca'}）")

    def rank(g):
        key = g["label"].upper()
        return (g["product"] != "bianca", ORDER.index(key) if key in ORDER else 99, key)
    groups.sort(key=rank)

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    ut = cfg.setdefault("utilization", {})
    ut["groups"] = groups
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已更新 config.json：{len(groups)} 個群組。請重新啟動 onedrive_worker.py。")


if __name__ == "__main__":
    main()

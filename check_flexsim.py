"""
FlexSim 連線檢查（約 1 分鐘）
----------------------------
在正式啟動 onedrive_worker.py 之前，先確認：
  1. 找得到 flexsim.exe 和模型檔
  2. 能用命令列在背景開啟模型、執行腳本、自動關閉
  3. 讀得到 WUS_year 表（第 1 列第 1 欄）
  4. 完整跑一次預設參數的模擬（可略過）

執行：python check_flexsim.py
     python check_flexsim.py --quick   （只做 1～3，不跑完整模擬）
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime

from runner import (RUNS_DIR, FlexSimRunner, _fs_str, find_flexsim_exe, load_config, parse_results, parse_tables,
                    table_export_lines, validate_params)


def main() -> None:
    cfg = load_config()
    print("== 1. 尋找 FlexSim 和模型 ==")
    try:
        exe = find_flexsim_exe(cfg)
    except RuntimeError as e:
        sys.exit(f"  ✗ {e}")
    print(f"  ✓ FlexSim: {exe}")
    from pathlib import Path
    model = Path(cfg["flexsim"]["model_path"])
    if not model.is_file():
        sys.exit(f"  ✗ 找不到模型：{model}（請檢查 config.json 的 model_path）")
    print(f"  ✓ 模型:    {model}")

    print("\n== 2. 背景開啟模型並執行小腳本 ==")
    run_dir = RUNS_DIR / ("check-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True)
    script, result, tables = run_dir / "script.txt", run_dir / "result.txt", run_dir / "tables.txt"
    di = cfg.get("daily_input")
    sched_check = "".join(
        f'Table wq{i} = Table({_fs_str(p["schedule_table"])}); wq{i}.setSize(wq{i}.numRows, Math.max(7, wq{i}.numCols)); '
        f'fpt("sched_{p["name"]}\\t" + numtostring(wq{i}.numCols, 0, 0)); fpr();\n'
        for i, p in enumerate(di["products"], 1)) if di else ""
    export = "\n".join(table_export_lines(cfg.get("tables", []), tables)) + "\n" if cfg.get("tables") else ""
    script.write_text(
        f'fileopen({_fs_str(str(result))}, "w");\n'
        'fpt("model_time\\t" + numtostring(Model.time, 0, 6)); fpr();\n'
        'fpt("wus_year_r1c1\\t" + numtostring(Table("WUS_year")[1][1], 0, 6)); fpr();\n' + sched_check +
        'fpt("__done\\t1"); fpr();\n'
        "fileclose();\n" + export +
        "cmdexit();\n", encoding="utf-8")
    cmd = [str(exe), str(model), "/maintenance", "nogui_disablemsg_runscript", "/scriptpath", str(script)]
    print("  執行中（第一次開啟 FlexSim 可能要 10～60 秒）…")
    t0 = time.time()
    try:
        subprocess.run(cmd, cwd=str(run_dir), timeout=300)
    except subprocess.TimeoutExpired:
        sys.exit("  ✗ FlexSim 5 分鐘內沒有自己關閉。\n"
                 "    可能原因：FlexSim 跳出對話框（例如授權、存檔詢問）卡住了。請看工作管理員是否有 flexsim.exe，拍照給我。")
    took = time.time() - t0
    if not result.exists():
        sys.exit(f"  ✗ FlexSim 已關閉（{took:.0f} 秒），但沒有產生結果檔。\n"
                 f"    命令列腳本可能沒有被執行。腳本在：{script}")
    values = parse_results(result)
    print(f"  ✓ FlexSim 背景執行並自動關閉，花了 {took:.0f} 秒")

    print("\n== 3. 讀取 WUS_year ==")
    v = values.get("wus_year_r1c1")
    if v is None:
        sys.exit("  ✗ 讀不到 Table(\"WUS_year\")[1][1]")
    print(f"  ✓ WUS_year 第 1 列第 1 欄 = {v:g}（應該是年份，例如 2026）")

    if di:
        print("\n== 3a. 每日投產表 ==")
        for p in di["products"]:
            n = values.get(f"sched_{p['name']}")
            if n is None:
                sys.exit(f"  ✗ 無法操作 {p['schedule_table']}（setSize 或表名有問題）。請把 {script} 貼到 Script Console 執行看哪一行出錯。")
            print(f"  ✓ {p['schedule_table']}: {n:g} 欄（需要至少 7 欄：Month … Holiday）")

    if cfg.get("tables"):
        print("\n== 3b. 匯出輸出表格 ==")
        text = tables.read_text(encoding="utf-8", errors="replace") if tables.exists() else ""
        if "@done" not in text:
            sys.exit(f"  ✗ 表格沒有完整匯出。請把 {script} 的內容貼到 FlexSim 的 Script Console 執行，看哪一行出錯，拍照給我。")
        for name, tb in parse_tables(tables, cfg["tables"]).items():
            first = tb["rows"][0] if tb["rows"] else None
            print(f"  ✓ {name}: {len(tb['rows'])} 列，欄位 {', '.join(tb['columns'])}")
            if first:
                print(f"      第一列 {first['row']}: {first['values']}")

    if "--quick" in sys.argv:
        print("\n快速檢查完成。可以執行 python onedrive_worker.py")
        return

    print("\n== 4. 用預設參數完整跑一次模擬 ==")
    params = {p["name"]: p["default"] for p in cfg["parameters"]}
    clean, length = validate_params(cfg, params, None)
    print(f"  參數：{clean}")
    print("  執行中（時間依模型大小而定）…")
    out = FlexSimRunner(cfg).run(clean, length)
    print(f"  ✓ 完成，花了 {out['wall_seconds']} 秒，模型時間 {out['model_time']}")
    for m in cfg["metrics"]:
        print(f"    {m['label']}: {out['metrics'].get(m['name'])} {m.get('unit', '')}")
    print("\n全部檢查完成！可以執行 python onedrive_worker.py")


if __name__ == "__main__":
    main()

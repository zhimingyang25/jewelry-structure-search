"""check_in_library：验收前先确认目标图是否在 images.json 里（design.md §9「验收对子不在库里」）。

  py -3.11 -m search_service.check_in_library 文件名或路径 [更多…]
不需要加载模型。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import load_config


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：py -3.11 -m search_service.check_in_library 文件名或完整路径 …")
        sys.exit(1)
    cfg = load_config()
    catalog = json.loads((cfg.data_dir / "images.json").read_text("utf-8")).get("images", [])
    by_path = {os.path.normpath(im["path"]).lower(): im for im in catalog}
    by_name: dict[str, list[dict]] = {}
    for im in catalog:
        by_name.setdefault(im["file_name"].lower(), []).append(im)
    indexed: set[str] = set()
    idx_root = cfg.index_dir
    if idx_root.exists():
        for meta in idx_root.glob("*/meta.json"):
            try:
                for it in json.loads(Path(meta).read_text("utf-8")).get("items", []):
                    indexed.add(os.path.normpath(it["path"]).lower())
            except Exception:  # noqa: BLE001
                pass
    for q in sys.argv[1:]:
        key = os.path.normpath(q).lower()
        hits = [by_path[key]] if key in by_path else by_name.get(os.path.basename(q).lower(), [])
        if not hits:
            print(f"不在图库清单里：{q}   → 这一组不能计入 A1，需要先扫描它所在的文件夹")
            continue
        for im in hits:
            flag = "已认" if os.path.normpath(im["path"]).lower() in indexed else "在清单但未认"
            print(f"{flag}：{im['path']}")


if __name__ == "__main__":
    main()

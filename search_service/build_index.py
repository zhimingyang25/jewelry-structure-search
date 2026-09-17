"""命令行建索引（等价于网页「开始认图」，方便过夜跑或试跑）。

用法：
  py -3.11 -m search_service.build_index                 增量认图
  py -3.11 -m search_service.build_index --limit=500     只认 500 张试跑
  py -3.11 -m search_service.build_index --rebuild       删掉现有索引全量重建
"""
from __future__ import annotations

import sys
import time

from ._cli import Ctx, parse_args


def main() -> None:
    args = parse_args(sys.argv[1:])
    ctx = Ctx(need_index=False)
    limit = int(args["limit"]) if args.get("limit") else None
    ctx.indexer.start(rebuild=bool(args.get("rebuild")), limit=limit)
    last = ""
    try:
        while ctx.indexer.progress.running:
            s = ctx.indexer.progress.snapshot()
            line = f"[{s['phase']}] {s['done']}/{s['total']}  {s['rate_per_sec']}/s  失败 {s['failed']}  {s['message']}"
            if s.get("eta_sec") is not None:
                line += f"  预计还需 {s['eta_sec'] // 60} 分钟"
            if line != last:
                print(line, flush=True)
                last = line
            time.sleep(2)
    except KeyboardInterrupt:
        print("收到停止信号，正在保存已认部分…", flush=True)
        ctx.indexer.stop()
        ctx.indexer.join()
    s = ctx.indexer.progress.snapshot()
    print(f"结束：{s['phase']}  已认 {s['done']}  失败 {s['failed']}  {s['error'] or ''}", flush=True)
    print(f"索引目录：{ctx.store.dir}  共 {len(ctx.store.items)} 张", flush=True)


if __name__ == "__main__":
    main()

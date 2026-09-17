"""smoke：随机 N 张已索引库图，各自当查询，自身排第一即通过；邻居只报告不判定（design.md §10.2）。

  py -3.11 -m search_service.smoke --n=20
"""
from __future__ import annotations

import os
import sys
import time

from ._cli import Ctx, parse_args
from .embedder import open_image
from .index_store import file_sha1


def main() -> None:
    args = parse_args(sys.argv[1:])
    n = int(args.get("n", 20))
    ctx = Ctx()
    sample = ctx.sample_indexed(n)
    passed = 0
    times = []
    for it in sample:
        try:
            _, data = file_sha1(it["path"])
            img = open_image(data, ctx.embedder.side)
        except Exception as exc:  # noqa: BLE001
            print(f"跳过 {it['path']}：{exc}")
            continue
        cat = ctx.store.categories.get(it["sha1"], {})
        group = (cat.get("groups") or ["other"])[0]
        t0 = time.time()
        q = ctx.embedder.embed_query(img, None)
        cf, _ = ctx.clip.classify([img])
        r = ctx.searcher.search(q, group, cf[0])
        dt = time.time() - t0
        times.append(dt)
        rank = next((i + 1 for i, x in enumerate(r.results) if x["sha1"] == it["sha1"]), None)
        ok = rank == 1
        passed += ok
        folder = os.path.dirname(it["path"])
        same_folder_top10 = sum(1 for x in r.results[1:11] if os.path.dirname(x["path"]) == folder)
        print(f"{'通过' if ok else '未通过'}  自身名次={rank}  top1={r.top_score:.3f}  无同款提示={r.no_close_match}  同文件夹进前10={same_folder_top10}  {dt * 1000:.0f}ms  {os.path.basename(it['path'])}")
    print(f"\n{passed}/{len(times)} 自身排第一；平均查询 {sum(times) / max(1, len(times)) * 1000:.0f} ms（不含 GPT）")


if __name__ == "__main__":
    main()

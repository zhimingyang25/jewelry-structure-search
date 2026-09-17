"""bench：随机 N 张库图实测建索引速度，外推全库耗时（design.md §10.1）。

  py -3.11 -m search_service.bench --n=200
"""
from __future__ import annotations

import random
import sys
import time

from ._cli import Ctx, parse_args
from .embedder import ImageDecodeError, open_image
from .index_store import file_sha1


def main() -> None:
    args = parse_args(sys.argv[1:])
    n = int(args.get("n", 200))
    ctx = Ctx(need_index=False)
    catalog = ctx.catalog()
    sample = random.Random(0).sample(catalog, min(n, len(catalog)))
    bs = ctx.cfg.batch_size
    t_read = t_embed = t_clip = 0.0
    done = failed = 0
    imgs = []
    for im in sample:
        t0 = time.time()
        try:
            _, data = file_sha1(im["path"])
            img = open_image(data, ctx.embedder.side)
        except (OSError, ImageDecodeError):
            failed += 1
            continue
        t_read += time.time() - t0
        imgs.append(img)
        if len(imgs) >= bs:
            t1 = time.time()
            ctx.embedder.embed_library_batch(imgs)
            t_embed += time.time() - t1
            t2 = time.time()
            ctx.clip.classify(imgs)
            t_clip += time.time() - t2
            done += len(imgs)
            imgs = []
    if imgs:
        t1 = time.time()
        ctx.embedder.embed_library_batch(imgs)
        t_embed += time.time() - t1
        t2 = time.time()
        ctx.clip.classify(imgs)
        t_clip += time.time() - t2
        done += len(imgs)
    total = t_read + t_embed + t_clip
    per = total / max(1, done)
    full = len(catalog)
    print(f"样本 {done} 张（失败 {failed}），设备 {ctx.embedder.device}，变体 {ctx.cfg.variants}")
    print(f"读图+解码 {t_read / max(1, done) * 1000:.0f} ms/张，DINOv2 {t_embed / max(1, done) * 1000:.0f} ms/张，CLIP {t_clip / max(1, done) * 1000:.0f} ms/张")
    print(f"合计 {per * 1000:.0f} ms/张 → 全库 {full} 张约 {per * full / 3600:.1f} 小时")


if __name__ == "__main__":
    main()

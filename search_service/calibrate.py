"""calibrate：库内扰动校准，只定「近似重复」的分数上界（design.md §3.7）。

跨域阈值不能从这里得出，要靠 eval 的真实对子；本工具给的是「同一张图经过重压/裁边/翻转后大概能到多少分」，
用来确认阈值没有高到连自己都认不出。

  py -3.11 -m search_service.calibrate --n=300
"""
from __future__ import annotations

import io
import sys

import numpy as np
from PIL import Image, ImageOps

from ._cli import Ctx, parse_args
from .embedder import open_image
from .index_store import file_sha1


def perturb(img: Image.Image) -> Image.Image:
    w, h = img.size
    cropped = img.crop((int(w * 0.04), int(h * 0.04), int(w * 0.96), int(h * 0.96)))
    mirrored = ImageOps.mirror(cropped)
    buf = io.BytesIO()
    mirrored.save(buf, "JPEG", quality=60)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def main() -> None:
    args = parse_args(sys.argv[1:])
    n = int(args.get("n", 300))
    ctx = Ctx()
    self_scores, second_scores, tail_gaps = [], [], []
    by_group: dict[str, list[float]] = {}
    for it in ctx.sample_indexed(n, seed=1):
        try:
            _, data = file_sha1(it["path"])
            img = perturb(open_image(data, ctx.embedder.side))
        except Exception:  # noqa: BLE001
            continue
        group = (ctx.store.categories.get(it["sha1"], {}).get("groups") or ["other"])[0]
        q = ctx.embedder.embed_query(img, None)
        cf, _ = ctx.clip.classify([img])
        r = ctx.searcher.search(q, group, cf[0])
        me = next((x["score"] for x in r.results if x["sha1"] == it["sha1"]), None)
        if me is None:
            continue
        others = [x["score"] for x in r.results if x["sha1"] != it["sha1"]]
        self_scores.append(me)
        by_group.setdefault(group, []).append(me)
        if others:
            second_scores.append(others[0])
        if len(others) >= 40:
            tail_gaps.append(me - float(np.median(others[19:100])))
    s = np.array(self_scores)
    print(f"样本 {len(s)} 张。扰动后自身分：均值 {s.mean():.3f}，下四分位 {np.percentile(s, 25):.3f}，最低 {s.min():.3f}")
    if second_scores:
        o = np.array(second_scores)
        print(f"第二名分：均值 {o.mean():.3f}，上四分位 {np.percentile(o, 75):.3f}，最高 {o.max():.3f}")
    if tail_gaps:
        g = np.array(tail_gaps)
        print(f"自身 − 第20~100名中位：均值 {g.mean():.3f}，下四分位 {np.percentile(g, 25):.3f}")
    for grp, vals in sorted(by_group.items()):
        v = np.array(vals)
        print(f"  {grp}: n={len(v)} 自身分下四分位 {np.percentile(v, 25):.3f}")
    print(f"\n当前 absThreshold={ctx.cfg.abs_threshold} gapThreshold={ctx.cfg.gap_threshold}")
    print("提示：absThreshold 不应高于「自身分下四分位」；跨域（精修图 vs 渲染图）的真实阈值请用 eval 的对子决定，通常要比这里低不少。")


if __name__ == "__main__":
    main()

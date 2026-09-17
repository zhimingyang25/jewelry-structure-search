"""eval：用真实对子评测名次（design.md §10.3）。验收对子只当测试集，不用来调参。

对子文件 eval/pairs.json：
[
  {"query": "C:/客人图/a.jpg", "targets": ["E:/.../库里那张.jpg", "..."], "category": "ring", "note": "可选"},
  {"query": "C:/客人图/b.jpg", "targets": [], "no_match": true}     ← 用户确认库里没同款的图
]
category 可省略（用 CLIP 判）。targets 用库内绝对路径，或只写文件名（会按文件名在索引里找）。

  py -3.11 -m search_service.eval --pairs=eval/pairs.json
  py -3.11 -m search_service.eval --pairs=eval/pairs.json --sweep     同时试 仅彩色/仅灰度/开CLIP融合
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ._cli import Ctx, parse_args
from .embedder import open_image
from .index_store import file_sha1


def find_rows(ctx: Ctx, targets: list[str]) -> list[int]:
    by_path = ctx.path_to_row()
    by_name: dict[str, list[int]] = {}
    for i, it in enumerate(ctx.store.items):
        by_name.setdefault(os.path.basename(it["path"]).lower(), []).append(i)
    rows: list[int] = []
    for t in targets:
        t_norm = os.path.normpath(t)
        if t_norm in by_path:
            rows.append(by_path[t_norm])
        else:
            rows.extend(by_name.get(os.path.basename(t).lower(), []))
    return sorted(set(rows))


def run_once(ctx: Ctx, pairs: list[dict], label: str) -> dict:
    lines = []
    ranks = []
    no_match_ok = 0
    no_match_total = 0
    for p in pairs:
        try:
            _, data = file_sha1(p["query"])
            img = open_image(data, ctx.embedder.side)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  跳过 {p['query']}：{exc}")
            continue
        q = ctx.embedder.embed_query(img, p.get("box"))
        cf, cr = ctx.clip.classify([img])
        group = ctx.cfg.group_of(p.get("category") or cr[0]["group"])
        r = ctx.searcher.search(q, group, cf[0], limit=1000)
        if p.get("no_match"):
            no_match_total += 1
            no_match_ok += bool(r.no_close_match)
            lines.append(f"  [无同款图] 提示={'有' if r.no_close_match else '无'}  top1={r.top_score:.3f}  {os.path.basename(p['query'])}")
            continue
        rows = find_rows(ctx, p.get("targets", []))
        target_shas = {ctx.store.items[i]["sha1"] for i in rows}
        rank = next((i + 1 for i, x in enumerate(r.results) if x["sha1"] in target_shas), None)
        ranks.append(rank)
        tscore = next((x["score"] for x in r.results if x["sha1"] in target_shas), None)
        lines.append(f"  名次={rank if rank else '>1000'}  目标分={tscore}  top1={r.top_score:.3f}  无同款提示={r.no_close_match}  品类={group}({'指定' if p.get('category') else 'clip'})  框={r.box_source}  {os.path.basename(p['query'])}"
                     + ("  ⚠ 目标不在索引里" if not rows else ""))
    valid = [x for x in ranks if x]
    top20 = sum(1 for x in ranks if x and x <= 20)
    summary = {
        "label": label, "pairs": len(ranks), "top20": top20, "top100": sum(1 for x in ranks if x and x <= 100),
        "missing": sum(1 for x in ranks if x is None), "median_rank": sorted(valid)[len(valid) // 2] if valid else None,
        "no_match_ok": f"{no_match_ok}/{no_match_total}",
    }
    print(f"\n=== {label} ===")
    print("\n".join(lines))
    print(f"  进前20：{top20}/{len(ranks)}  进前100：{summary['top100']}/{len(ranks)}  完全没找到：{summary['missing']}  中位名次：{summary['median_rank']}  无同款图正确提示：{summary['no_match_ok']}")
    return summary


def main() -> None:
    args = parse_args(sys.argv[1:])
    pairs_path = Path(args.get("pairs", "eval/pairs.json"))
    if not pairs_path.exists():
        print(f"找不到 {pairs_path}。请按本文件顶部说明准备对子。")
        sys.exit(1)
    pairs = json.loads(pairs_path.read_text("utf-8"))
    ctx = Ctx()
    cfg = ctx.cfg
    summaries = [run_once(ctx, pairs, f"默认 colors={cfg.color_modes} clip_w={cfg.clip_fusion_weight}")]
    if args.get("sweep"):
        base_colors, base_w = list(cfg.color_modes), cfg.clip_fusion_weight
        for colors, w in [(["color"], 0.0), (["gray"], 0.0), (base_colors, 0.2), (base_colors, 0.35)]:
            if all(c in base_colors for c in colors):
                cfg.color_modes, cfg.clip_fusion_weight = colors, w
                summaries.append(run_once(ctx, pairs, f"colors={colors} clip_w={w}"))
        cfg.color_modes, cfg.clip_fusion_weight = base_colors, base_w
    print("\n汇总：")
    for s in summaries:
        print(f"  {s['label']}: 前20 {s['top20']}/{s['pairs']}，前100 {s['top100']}/{s['pairs']}，中位 {s['median_rank']}，无同款提示 {s['no_match_ok']}")


if __name__ == "__main__":
    main()

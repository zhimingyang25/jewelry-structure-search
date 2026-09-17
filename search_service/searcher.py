"""查询：品类过滤、多路融合、可选 CLIP 语义融合、无同款判定、完全重复合并（design.md §3.7/§3.8）。"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .category import ClipClassifier
from .config import SearchConfig
from .embedder import Embedder, QueryEmbedding
from .index_store import IndexStore


@dataclass
class SearchResult:
    results: list[dict]
    category_used: str
    top_score: float
    no_close_match: bool
    reason: str
    elapsed_ms: int
    box_used: tuple | None
    box_source: str


class Searcher:
    def __init__(self, cfg: SearchConfig, store: IndexStore, embedder: Embedder, clip: ClipClassifier):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.clip = clip

    def _group_mask(self, group: str) -> np.ndarray:
        cats = self.store.categories
        return np.array([group in cats.get(it["sha1"], {}).get("groups", ["other"]) for it in self.store.items], dtype=bool)

    def score_all(self, q: QueryEmbedding, clip_query: np.ndarray | None) -> np.ndarray:
        """每张库图一个分：同颜色的 full/crop 取最大，颜色间取平均，可选融合 CLIP。"""
        cfg, store = self.cfg, self.store
        per_color = []
        for color in cfg.color_modes:
            qv = q.vectors[color]
            best = None
            for view in cfg.views:
                arr = store.vectors.get(f"{color}_{view}")
                if arr is None or arr.shape[0] == 0:
                    continue
                s = arr @ qv
                best = s if best is None else np.maximum(best, s)
            if best is not None:
                per_color.append(best)
        score = np.mean(per_color, axis=0) if per_color else np.zeros(len(store.items), dtype=np.float32)
        if cfg.clip_fusion_weight > 0 and clip_query is not None and store.clip_vectors is not None:
            cs = store.clip_vectors @ clip_query
            score = (1 - cfg.clip_fusion_weight) * score + cfg.clip_fusion_weight * cs
        return score.astype(np.float32)

    def search(self, q: QueryEmbedding, group: str, clip_query: np.ndarray | None, limit: int | None = None) -> SearchResult:
        t0 = time.time()
        cfg, store = self.cfg, self.store
        limit = limit or cfg.top_k
        n = len(store.items)
        if n == 0:
            return SearchResult([], group, 0.0, True, "index_empty", 0, q.box_used, q.box_source)
        score = self.score_all(q, clip_query)
        mask = self._group_mask(group)
        score_masked = np.where(mask, score, -np.inf)
        order = np.argsort(-score_masked)
        results: list[dict] = []
        seen_sha: set[str] = set()
        for i in order:
            if not np.isfinite(score_masked[i]):
                break
            it = store.items[i]
            if it["sha1"] in seen_sha:  # 完全重复的文件只留一条（同一文件，不是按款分组）
                continue
            seen_sha.add(it["sha1"])
            results.append({"sha1": it["sha1"], "path": it["path"], "score": round(float(score[i]), 4)})
            if len(results) >= limit:
                break
        top = results[0]["score"] if results else 0.0
        no_close, reason = self._judge_no_close(results, group)
        return SearchResult(results, group, top, no_close, reason, int((time.time() - t0) * 1000), q.box_used, q.box_source)

    def _judge_no_close(self, results: list[dict], group: str) -> tuple[bool, str]:
        cfg = self.cfg
        if not results:
            return True, "no_results"
        top = results[0]["score"]
        if top < cfg.abs_threshold_for(group):
            return True, "below_abs_threshold"
        tail = [r["score"] for r in results[19:100]]
        if tail:
            gap = top - float(np.median(tail))
            if gap < cfg.gap_threshold:
                return True, "gap_too_small"
        return False, "ok"

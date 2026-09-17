"""品类：CLIP 零样本分类 + 旧标签合并规则（design.md §3.3）。

- 库图：全库跑 CLIP；旧标签与 CLIP 一致取之，不一致两者都挂（多标签），任一品类搜索都能命中。
- 查询图：GPT 失败或 unclear 时用 CLIP；置信不够则返回 uncertain，让用户手选。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
from PIL import Image

from .config import CLIP_PROMPTS_VERSION, SearchConfig, resolve_model_path
from .embedder import l2norm, letterbox_white


def clip_signature(cfg: SearchConfig) -> str:
    payload = json.dumps({"model": cfg.clip_model, "prompts": cfg.clip_prompts, "v": CLIP_PROMPTS_VERSION}, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


class ClipClassifier:
    def __init__(self, cfg: SearchConfig):
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self.cfg = cfg
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        path = resolve_model_path(cfg, cfg.clip_model)
        self.model = CLIPModel.from_pretrained(path).to(self.device).eval()
        self.processor = CLIPProcessor.from_pretrained(path)
        self.groups = list(cfg.clip_prompts.keys())
        self.text = self._text_embeddings()
        self.dim = int(self.model.config.projection_dim)

    def _text_embeddings(self) -> np.ndarray:
        torch = self.torch
        vecs = []
        with torch.inference_mode():
            for g in self.groups:
                prompts = self.cfg.clip_prompts[g]
                inputs = self.processor(text=prompts, return_tensors="pt", padding=True).to(self.device)
                t = self.model.get_text_features(**inputs)
                t = torch.nn.functional.normalize(t, dim=-1).mean(dim=0)
                vecs.append(torch.nn.functional.normalize(t, dim=0).float().cpu().numpy())
        return np.stack(vecs)

    def image_features(self, images: list[Image.Image]) -> np.ndarray:
        torch = self.torch
        prepped = [letterbox_white(im, 224) for im in images]
        inputs = self.processor(images=prepped, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            f = self.model.get_image_features(**inputs)
        return l2norm(f.float().cpu().numpy())

    def classify_features(self, feats: np.ndarray) -> list[dict]:
        logits = 100.0 * feats @ self.text.T
        logits = logits - logits.max(axis=1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=1, keepdims=True)
        out = []
        for p in probs:
            order = np.argsort(-p)
            out.append({
                "group": self.groups[order[0]],
                "prob": float(p[order[0]]),
                "margin": float(p[order[0]] - p[order[1]]) if len(order) > 1 else 1.0,
            })
        return out

    def classify(self, images: list[Image.Image]) -> tuple[np.ndarray, list[dict]]:
        feats = self.image_features(images)
        return feats, self.classify_features(feats)

    def is_confident(self, r: dict) -> bool:
        return r["prob"] >= self.cfg.clip_min_prob and r["margin"] >= self.cfg.clip_min_margin


def merge_groups(cfg: SearchConfig, tag_category: str | None, clip_group: str | None) -> list[str]:
    """库图归属组：旧标签组 ∪ CLIP 组。旧标签为 other/unclear 时只信 CLIP。"""
    groups: list[str] = []
    if clip_group:
        groups.append(clip_group)
    if tag_category:
        g = cfg.group_of(tag_category)
        if g != "other" and g not in groups:
            groups.append(g)
    if not groups:
        groups.append("other")
    return groups

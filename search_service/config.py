"""读取 config.json 的 searchService 段与 .env，给出带默认值的配置对象。

技术拍板都集中在这里，方便实施后按 eval 结果改默认值而不改代码。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 品类组：库图与查询图都映射到这些组后再做硬过滤。
# 吊坠/项链/挂饰合并：同一款吊坠渲染图两种标法都常见，属同一物件（design.md §3.3）。
# 验收时若用户不认可，改这张表即可，不需要重建向量索引。
DEFAULT_CATEGORY_GROUPS = {
    "ring": ["ring"],
    "pendant": ["pendant", "necklace", "charm"],
    "earring": ["earring"],
    "bracelet": ["bracelet"],
    "bangle": ["bangle"],
    "brooch": ["brooch"],
    "other": ["other", "unclear"],
}

# CLIP 零样本分类用的提示词。改动这里只影响 categories.json 的签名，不触发向量重建。
DEFAULT_CLIP_PROMPTS = {
    "ring": ["a photo of a ring", "a jewelry ring", "a 3D render of a finger ring"],
    "pendant": ["a photo of a pendant", "a necklace pendant", "a 3D render of a jewelry pendant"],
    "earring": ["a photo of earrings", "a pair of earrings", "a 3D render of an earring"],
    "bracelet": ["a photo of a bracelet", "a chain bracelet", "a 3D render of a bracelet"],
    "bangle": ["a photo of a bangle", "a rigid bangle", "a 3D render of a bangle"],
    "brooch": ["a photo of a brooch", "a jewelry brooch pin", "a 3D render of a brooch"],
}

PREPROCESS_VERSION = "v2-letterbox-white-448"
CLIP_PROMPTS_VERSION = "v1"


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k.strip()] = v
    return out


@dataclass
class SearchConfig:
    port: int = 8788
    data_dir: Path = ROOT / "data"
    index_dir: Path = ROOT / "data" / "index"
    models_dir: Path = ROOT / "models"
    embed_model: str = "facebook/dinov2-base"
    clip_model: str = "openai/clip-vit-base-patch32"
    input_size: int = 448
    # 库图向量变体：颜色 × 视图。查询时对同一颜色的 full/crop 取最大，再对颜色取平均。
    color_modes: list[str] = field(default_factory=lambda: ["color", "gray"])
    views: list[str] = field(default_factory=lambda: ["full", "crop"])
    # CLIP 语义分融合权重，默认关（0）。A7「另一种头不要排太前」的备选开关。
    clip_fusion_weight: float = 0.0
    # 二阶段 patch 级重排，默认关。开了会重新读取前 rerank_top 张库图，GPU 下约 10 秒。
    rerank_enabled: bool = False
    rerank_top: int = 200
    # 查询 TTA 视角数：GPU 8（4 旋转 × 翻转），CPU 自动降到 2。
    tta_views_gpu: int = 8
    tta_views_cpu: int = 2
    top_k: int = 100
    # 「没找到很像的同款」判定：任一成立即提示（design.md §3.7）。阈值先取保守值，宁可多提示。
    abs_threshold: dict[str, float] = field(default_factory=lambda: {"default": 0.62})
    gap_threshold: float = 0.08
    category_groups: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_CATEGORY_GROUPS))
    clip_prompts: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_CLIP_PROMPTS))
    # CLIP 判查询图品类时的置信门槛；低于则不自动搜，请用户选。
    clip_min_prob: float = 0.5
    clip_min_margin: float = 0.15
    batch_size: int = 16
    flush_every: int = 500
    max_body_bytes: int = 20 * 1024 * 1024
    query_cache_size: int = 5
    query_cache_ttl_sec: int = 1800
    env: dict[str, str] = field(default_factory=dict)

    @property
    def variants(self) -> list[str]:
        return [f"{c}_{v}" for c in self.color_modes for v in self.views]

    def abs_threshold_for(self, group: str) -> float:
        return float(self.abs_threshold.get(group, self.abs_threshold.get("default", 0.62)))

    def group_of(self, raw_category: str | None) -> str:
        raw = (raw_category or "unclear").strip().lower()
        for group, members in self.category_groups.items():
            if raw == group or raw in members:
                return group
        return "other"


def load_config() -> SearchConfig:
    cfg = SearchConfig()
    raw = {}
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text("utf-8"))
    data_dir = raw.get("dataDir") or "data"
    cfg.data_dir = (ROOT / data_dir).resolve()
    cfg.index_dir = cfg.data_dir / "index"
    s = raw.get("searchService") or {}
    cfg.port = int(s.get("port", cfg.port))
    cfg.embed_model = s.get("embedModel", cfg.embed_model)
    cfg.clip_model = s.get("clipModel", cfg.clip_model)
    cfg.input_size = int(s.get("inputSize", cfg.input_size))
    cfg.color_modes = list(s.get("colorModes", cfg.color_modes))
    cfg.views = list(s.get("views", cfg.views))
    cfg.clip_fusion_weight = float(s.get("clipFusionWeight", cfg.clip_fusion_weight))
    cfg.rerank_enabled = bool(s.get("rerankEnabled", cfg.rerank_enabled))
    cfg.rerank_top = int(s.get("rerankTop", cfg.rerank_top))
    cfg.top_k = int(s.get("topK", raw.get("topK", cfg.top_k)))
    if isinstance(s.get("absThreshold"), dict):
        cfg.abs_threshold = {k: float(v) for k, v in s["absThreshold"].items()}
    elif s.get("absThreshold") is not None:
        cfg.abs_threshold = {"default": float(s["absThreshold"])}
    cfg.gap_threshold = float(s.get("gapThreshold", cfg.gap_threshold))
    if isinstance(s.get("categoryGroups"), dict):
        cfg.category_groups = {k: list(v) for k, v in s["categoryGroups"].items()}
    cfg.batch_size = int(s.get("batchSize", cfg.batch_size))
    cfg.clip_min_prob = float(s.get("clipMinProb", cfg.clip_min_prob))
    cfg.clip_min_margin = float(s.get("clipMinMargin", cfg.clip_min_margin))
    cfg.env = _load_env(ROOT / ".env")
    # HF 镜像：.env 里 HF_ENDPOINT=https://hf-mirror.com 可加速国内下载
    for key in ("HF_ENDPOINT", "HF_HOME", "HF_HUB_OFFLINE"):
        if key in cfg.env and key not in os.environ:
            os.environ[key] = cfg.env[key]
    return cfg


def resolve_model_path(cfg: SearchConfig, name: str) -> str:
    """优先用 models/ 下的离线包（目录名把 / 换成 __），否则交给 transformers 从 HF 下载。"""
    local = cfg.models_dir / name.replace("/", "__")
    if local.exists():
        return str(local)
    return name

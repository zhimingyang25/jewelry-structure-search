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


def _hf_hub_cache_dir() -> Path:
    """复刻 huggingface_hub 的缓存目录规则；故意不 import 它，因为它一 import 就把 HF_HUB_OFFLINE 读死了。"""
    hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(os.environ.get("HF_HUB_CACHE") or (Path(hf_home) / "hub"))


def cached_snapshot(name: str) -> Path | None:
    """模型已完整下载到本机 HF 缓存时返回快照目录，否则 None。完整 = 有 config.json 且有权重文件。"""
    repo = _hf_hub_cache_dir() / ("models--" + name.replace("/", "--"))
    ref = repo / "refs" / "main"
    if not ref.exists():
        return None
    snap = repo / "snapshots" / ref.read_text("utf-8").strip()
    if not (snap / "config.json").exists():
        return None
    if not any((snap / w).exists() for w in ("model.safetensors", "pytorch_model.bin")):
        return None
    return snap


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
    # 建索引批大小。P104-100 实测 8 张一批最快（每张约 0.1 s 一次前向），显存峰值约 1.1 GB。
    batch_size: int = 8
    # 落盘策略：每 flush_every 张或每 flush_seconds 秒，先到者为准。关窗口是硬杀，这里决定最多丢多少。
    # 每次落盘要整份重写（5 万张约 1.3 GB，几秒），所以不能太勤。
    flush_every: int = 1000
    flush_seconds: int = 120
    max_body_bytes: int = 20 * 1024 * 1024
    query_cache_size: int = 5
    query_cache_ttl_sec: int = 1800
    env: dict[str, str] = field(default_factory=dict)

    @property
    def variants(self) -> list[str]:
        return [f"{c}_{v}" for c in self.color_modes for v in self.views]

    def abs_threshold_for(self, group: str) -> float:
        return float(self.abs_threshold.get(group, self.abs_threshold.get("default", 0.62)))

    def model_is_local(self, name: str) -> bool:
        """models/ 离线包、显式本地路径、或 HF 缓存里已完整下载，三者任一成立。"""
        if (self.models_dir / name.replace("/", "__")).exists():
            return True
        if Path(name).exists():
            return True
        return cached_snapshot(name) is not None

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
    # JSS_INDEX_DIR：测试时把索引指到别处，不碰正式索引
    cfg.index_dir = Path(os.environ["JSS_INDEX_DIR"]).resolve() if os.environ.get("JSS_INDEX_DIR") else cfg.data_dir / "index"
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
    cfg.flush_every = int(s.get("flushEvery", cfg.flush_every))
    cfg.flush_seconds = int(s.get("flushSeconds", cfg.flush_seconds))
    cfg.clip_min_prob = float(s.get("clipMinProb", cfg.clip_min_prob))
    cfg.clip_min_margin = float(s.get("clipMinMargin", cfg.clip_min_margin))
    cfg.env = _load_env(ROOT / ".env")
    # HF 镜像：.env 里 HF_ENDPOINT=https://hf-mirror.com 可加速国内下载
    for key in ("HF_ENDPOINT", "HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE"):
        if key in cfg.env and key not in os.environ:
            os.environ[key] = cfg.env[key]
    # 两个模型都已在本机时直接离线加载。否则 transformers 每次启动都先去 huggingface.co 查更新，
    # 国内这个连接常被重置（10054），每个文件要重试约 30 秒，整个启动会卡好几分钟。
    # 必须在任何 huggingface_hub / transformers import 之前设置，所以放在这里而不是 Embedder 里。
    if "HF_HUB_OFFLINE" not in os.environ and cfg.model_is_local(cfg.embed_model) and cfg.model_is_local(cfg.clip_model):
        os.environ["HF_HUB_OFFLINE"] = "1"
    return cfg


def resolve_model_path(cfg: SearchConfig, name: str) -> str:
    """优先用 models/ 下的离线包（目录名把 / 换成 __），否则交给 transformers 从 HF 下载。"""
    local = cfg.models_dir / name.replace("/", "__")
    if local.exists():
        return str(local)
    return name

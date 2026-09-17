"""DINOv2 向量器：图片解码、留白缩放、灰度、注意力前景框、TTA、批量向量。

技术拍板（design.md §3.1/§3.4/§3.6）：
- 向量 = CLS 与 patch 平均值拼接后 L2 归一化（base 模型为 1536 维）。
- 留白成正方形时用白底：图库几乎都是白底渲染图，和库图统一。
- 前景框用最后一层 CLS 对 patch 的注意力：不额外引入检测模型。
- 推理固定 FP32：P104-100（Pascal）FP16 无加速。
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

from .config import SearchConfig, resolve_model_path

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

Box = tuple[float, float, float, float]  # 归一化 x0,y0,x1,y1


class ImageDecodeError(Exception):
    """图片打不开。查询图时转成中文提示；库图时进 failed.json。"""


def open_image(source: str | bytes, want_side: int) -> Image.Image:
    """解码为 RGB。透明合白底、CMYK 转换、EXIF 方向修正。JPEG 用 draft 加速大图解码。"""
    try:
        img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
        if img.format == "JPEG":
            # draft 只会把图缩到不小于目标的 1/2^n 尺寸，裁剪仍有足够分辨率
            img.draft("RGB", (want_side * 2, want_side * 2))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, rgba).convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.load()
        return img
    except Exception as exc:  # noqa: BLE001
        raise ImageDecodeError(str(exc)) from exc


def letterbox_white(img: Image.Image, side: int) -> Image.Image:
    w, h = img.size
    scale = side / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((nw, nh), Image.BICUBIC)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(resized, ((side - nw) // 2, (side - nh) // 2))
    return canvas


def to_gray_rgb(img: Image.Image) -> Image.Image:
    return ImageOps.grayscale(img).convert("RGB")


def crop_box(img: Image.Image, box: Box, expand: float = 0.12) -> Image.Image:
    w, h = img.size
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    x0, x1 = max(0.0, x0 - bw * expand), min(1.0, x1 + bw * expand)
    y0, y1 = max(0.0, y0 - bh * expand), min(1.0, y1 + bh * expand)
    return img.crop((int(x0 * w), int(y0 * h), max(int(x0 * w) + 1, int(x1 * w)), max(int(y0 * h) + 1, int(y1 * h))))


def valid_box(box: Box | None) -> bool:
    if not box or len(box) != 4:
        return False
    x0, y0, x1, y1 = box
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
        return False
    if x1 <= x0 or y1 <= y0:
        return False
    area = (x1 - x0) * (y1 - y0)
    return 0.05 <= area <= 0.95


def to_tensor_batch(images: list[Image.Image]) -> np.ndarray:
    arr = np.stack([np.asarray(im, dtype=np.float32) / 255.0 for im in images])
    arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(arr.transpose(0, 3, 1, 2))


def l2norm(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-8)


@dataclass
class QueryEmbedding:
    vectors: dict[str, np.ndarray]  # color_mode -> 向量（已做 TTA 平均与归一化）
    box_used: Box | None
    box_source: str  # gpt / attention / none


class Embedder:
    def __init__(self, cfg: SearchConfig):
        import torch
        from transformers import AutoModel

        self.cfg = cfg
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        path = resolve_model_path(cfg, cfg.embed_model)
        # eager 注意力才能拿到 attention 矩阵算前景框
        self.model = AutoModel.from_pretrained(path, attn_implementation="eager").to(self.device).eval()
        self.patch = int(getattr(self.model.config, "patch_size", 14))
        self.side = cfg.input_size - (cfg.input_size % self.patch)
        self.grid = self.side // self.patch
        self.dim = int(self.model.config.hidden_size) * 2
        self.tta_views = cfg.tta_views_gpu if self.device == "cuda" else cfg.tta_views_cpu

    # ---------- 前向 ----------
    def _forward(self, batch: np.ndarray, want_attention: bool):
        torch = self.torch
        x = torch.from_numpy(batch).to(self.device)
        with torch.inference_mode():
            out = self.model(pixel_values=x, output_attentions=want_attention)
        hidden = out.last_hidden_state  # [B, 1+N(+reg), C]
        n_special = hidden.shape[1] - self.grid * self.grid
        cls = hidden[:, 0]
        patches = hidden[:, n_special:]
        emb = torch.cat([cls, patches.mean(dim=1)], dim=-1)
        emb = torch.nn.functional.normalize(emb, dim=-1).float().cpu().numpy()
        attn_maps = None
        if want_attention:
            attn = out.attentions[-1]  # [B, heads, T, T]
            cls_to_patch = attn[:, :, 0, n_special:].mean(dim=1)  # [B, N]
            attn_maps = cls_to_patch.reshape(-1, self.grid, self.grid).float().cpu().numpy()
        return emb, attn_maps

    def attention_box(self, attn_map: np.ndarray, orig_size: tuple[int, int]) -> Box | None:
        """注意力图 → 前景框（归一化到原图坐标，考虑留白）。"""
        a = attn_map
        thr = a.mean() + 0.5 * a.std()
        mask = a >= thr
        if mask.sum() < 4:
            return None
        ys, xs = np.where(mask)
        gx0, gx1 = xs.min() / self.grid, (xs.max() + 1) / self.grid
        gy0, gy1 = ys.min() / self.grid, (ys.max() + 1) / self.grid
        # 留白反算：正方形画布中原图占据的区域
        w, h = orig_size
        scale = 1.0 / max(w, h)
        cw, ch = w * scale, h * scale
        ox, oy = (1 - cw) / 2, (1 - ch) / 2

        def unpad(v, o, c):
            return min(1.0, max(0.0, (v - o) / c)) if c > 0 else v

        box = (unpad(gx0, ox, cw), unpad(gy0, oy, ch), unpad(gx1, ox, cw), unpad(gy1, oy, ch))
        return box if valid_box(box) else None

    # ---------- 库图批量 ----------
    def embed_library_batch(self, images: list[Image.Image]) -> dict[str, np.ndarray]:
        """返回 {variant: [B, dim]}。variant 由 cfg.color_modes × cfg.views 决定。"""
        cfg = self.cfg
        out: dict[str, np.ndarray] = {}
        full_color = [letterbox_white(im, self.side) for im in images]
        need_crop = "crop" in cfg.views
        emb, attn = self._forward(to_tensor_batch(full_color), want_attention=need_crop)
        if "color" in cfg.color_modes and "full" in cfg.views:
            out["color_full"] = emb
        boxes: list[Box | None] = []
        if need_crop:
            boxes = [self.attention_box(attn[i], images[i].size) for i in range(len(images))]
        crops = [crop_box(im, b) if b else im for im, b in zip(images, boxes)] if need_crop else []
        for color in cfg.color_modes:
            for view in cfg.views:
                key = f"{color}_{view}"
                if key in out:
                    continue
                src = crops if view == "crop" else images
                prepped = [letterbox_white(to_gray_rgb(im) if color == "gray" else im, self.side) for im in src]
                out[key], _ = self._forward(to_tensor_batch(prepped), want_attention=False)
        return out

    # ---------- 查询 ----------
    def embed_query(self, img: Image.Image, gpt_box: Box | None) -> QueryEmbedding:
        cfg = self.cfg
        box_used, box_source = None, "none"
        if valid_box(gpt_box):
            box_used, box_source = tuple(gpt_box), "gpt"  # type: ignore[arg-type]
        else:
            _, attn = self._forward(to_tensor_batch([letterbox_white(img, self.side)]), want_attention=True)
            b = self.attention_box(attn[0], img.size)
            if b:
                box_used, box_source = b, "attention"
        base = crop_box(img, box_used) if box_used else img
        vectors: dict[str, np.ndarray] = {}
        for color in cfg.color_modes:
            src = to_gray_rgb(base) if color == "gray" else base
            views = self._tta_views(src)
            emb, _ = self._forward(to_tensor_batch([letterbox_white(v, self.side) for v in views]), False)
            vectors[color] = l2norm(emb.mean(axis=0))
        return QueryEmbedding(vectors=vectors, box_used=box_used, box_source=box_source)

    def _tta_views(self, img: Image.Image) -> list[Image.Image]:
        n = self.tta_views
        rots = [0, 90, 180, 270] if n >= 8 else ([0, 180] if n >= 4 else [0])
        views = []
        for r in rots:
            v = img.rotate(r, expand=True) if r else img
            views.append(v)
            if n >= 2:
                views.append(ImageOps.mirror(v))
        return views[: max(1, n)]

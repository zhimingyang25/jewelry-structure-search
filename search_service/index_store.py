"""索引存储：多路 .npy + meta.json + categories.json + failed.json（design.md §4）。

一致性规则（代替单独的进度日志）：
- 每次 flush 先写 *.tmp 再 os.replace，逐个文件替换。
- 加载时各数组与 meta 取最短行数，多出来的行丢弃并在下次建索引时重算。
  所以中途崩溃最多丢最后一批，不会出现向量与路径错位。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np

from .config import PREPROCESS_VERSION, SearchConfig


def index_signature(cfg: SearchConfig) -> str:
    payload = json.dumps({
        "model": cfg.embed_model,
        "input": cfg.input_size,
        "colors": cfg.color_modes,
        "views": cfg.views,
        "pre": PREPROCESS_VERSION,
    }, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def file_sha1(path: str, chunk: int = 1 << 20) -> tuple[str, bytes]:
    """返回 (sha1, 文件字节)。建索引时顺带读文件，不额外遍历。"""
    h = hashlib.sha1()
    buf = bytearray()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            buf.extend(b)
    return h.hexdigest(), bytes(buf)


def _replace_with_retry(tmp: Path, path: Path, tries: int = 6) -> None:
    """Windows 上目标文件若被杀毒软件或其它句柄短暂占用，os.replace 会抛 PermissionError；等一下再试。"""
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.5 * (i + 1))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    _replace_with_retry(tmp, path)


def _atomic_write_json(path: Path, obj) -> None:
    _atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _atomic_save_npy(path: Path, arr: np.ndarray) -> None:
    tmp = path.with_suffix(".npy.tmp")
    with open(tmp, "wb") as f:
        np.save(f, arr)
    _replace_with_retry(tmp, path)


class IndexStore:
    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg
        self.signature = index_signature(cfg)
        self.dir = cfg.index_dir / self.signature
        self.variants = cfg.variants
        self.vectors: dict[str, np.ndarray] = {}
        self.clip_vectors: np.ndarray | None = None
        self.items: list[dict] = []  # {sha1, path, size, mtime_ms}
        self.categories: dict[str, dict] = {}  # sha1 -> {tag, clip, prob, margin, groups}
        self.clip_signature: str | None = None
        self.failed: dict[str, str] = {}  # path -> reason
        self.meta_extra: dict = {}

    # ---------- 路径 ----------
    def vec_path(self, variant: str) -> Path:
        return self.dir / f"vectors_{variant}.npy"

    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    @property
    def cat_path(self) -> Path:
        return self.dir / "categories.json"

    @property
    def failed_path(self) -> Path:
        return self.dir / "failed.json"

    @property
    def clip_vec_path(self) -> Path:
        return self.dir / "vectors_clip.npy"

    # ---------- 加载 ----------
    def load(self) -> "IndexStore":
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.meta_path.exists():
            return self
        meta = json.loads(self.meta_path.read_text("utf-8"))
        items = meta.get("items", [])
        self.meta_extra = {k: v for k, v in meta.items() if k != "items"}
        arrays: dict[str, np.ndarray] = {}
        n = len(items)
        for v in self.variants:
            p = self.vec_path(v)
            if not p.exists():
                n = 0
                break
            # 整个读进内存，不用 mmap：Windows 上被映射着的文件无法被 os.replace 覆盖，会让后续保存全部失败。
            # 5 万张 × 4 路 × 1536 维 float32 ≈ 1.2 GB，设计预算之内。
            arr = np.load(p)
            arrays[v] = arr
            n = min(n, arr.shape[0])
        self.items = items[:n]
        self.vectors = {v: np.ascontiguousarray(arr[:n]) for v, arr in arrays.items()}
        if self.clip_vec_path.exists():
            c = np.load(self.clip_vec_path)
            self.clip_vectors = np.ascontiguousarray(c[: min(n, c.shape[0])])
            if self.clip_vectors.shape[0] < n:
                self.clip_vectors = None
        if self.cat_path.exists():
            cat = json.loads(self.cat_path.read_text("utf-8"))
            self.clip_signature = cat.get("clip_signature")
            self.categories = cat.get("items", {})
        if self.failed_path.exists():
            self.failed = json.loads(self.failed_path.read_text("utf-8")).get("failed", {})
        return self

    # ---------- 保存 ----------
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        n = len(self.items)
        for v in self.variants:
            arr = self.vectors.get(v)
            if arr is None:
                continue
            _atomic_save_npy(self.vec_path(v), np.ascontiguousarray(arr[:n], dtype=np.float32))
        if self.clip_vectors is not None:
            _atomic_save_npy(self.clip_vec_path, np.ascontiguousarray(self.clip_vectors[:n], dtype=np.float32))
        _atomic_write_json(self.meta_path, {
            "signature": self.signature,
            "model": self.cfg.embed_model,
            "input_size": self.cfg.input_size,
            "preprocess_version": PREPROCESS_VERSION,
            "variants": self.variants,
            **self.meta_extra,
            "items": self.items,
        })
        self.save_categories()
        _atomic_write_json(self.failed_path, {"failed": self.failed})

    def save_categories(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self.cat_path, {"clip_signature": self.clip_signature, "items": self.categories})

    # ---------- 查询辅助 ----------
    def sha1_rows(self) -> dict[str, int]:
        return {it["sha1"]: i for i, it in enumerate(self.items)}

    def append(self, items: list[dict], vecs: dict[str, np.ndarray], clip_vecs: np.ndarray | None) -> None:
        for v in self.variants:
            new = np.asarray(vecs[v], dtype=np.float32)
            self.vectors[v] = new if v not in self.vectors or self.vectors[v].size == 0 else np.concatenate([self.vectors[v], new])
        if clip_vecs is not None:
            cv = np.asarray(clip_vecs, dtype=np.float32)
            self.clip_vectors = cv if self.clip_vectors is None or self.clip_vectors.size == 0 else np.concatenate([self.clip_vectors, cv])
        self.items.extend(items)

    def keep_rows(self, keep: np.ndarray) -> None:
        """按布尔掩码保留行（删除库里已不存在的图）。"""
        if keep.all():
            return
        self.items = [it for it, k in zip(self.items, keep) if k]
        for v in list(self.vectors):
            self.vectors[v] = np.ascontiguousarray(self.vectors[v][keep])
        if self.clip_vectors is not None:
            self.clip_vectors = np.ascontiguousarray(self.clip_vectors[keep])

    def wipe(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.__init__(self.cfg)

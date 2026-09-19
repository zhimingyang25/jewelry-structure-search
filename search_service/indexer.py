"""后台建索引任务：增量、可停、可续、进度可查（design.md §4、§6.5）。

流程：
1. 读 images.json 与 structure-tags.json。
2. 已索引项按 path+size+mtime 快速匹配（不重读文件）；不匹配的读文件算 sha1，
   sha1 已存在则只更新路径（文件被移动），否则进待算队列。
3. 待算队列分批：解码 → DINOv2 多变体向量 → CLIP 品类 → 追加 → 每 flush_every 张落盘。
4. 结束时删除库里已不存在的行，写盘。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .category import ClipClassifier, clip_signature, merge_groups
from .config import SearchConfig
from .embedder import Embedder, ImageDecodeError, open_image
from .index_store import IndexStore, file_sha1


@dataclass
class Progress:
    running: bool = False
    phase: str = "idle"  # idle / scanning / embedding / categorizing / saving / done / stopped / error
    done: int = 0
    total: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    rate_per_sec: float = 0.0
    message: str = ""
    error: str = ""
    stop_requested: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self) -> dict:
        with self.lock:
            eta = None
            if self.running and self.rate_per_sec > 0 and self.total > self.done:
                eta = int((self.total - self.done) / self.rate_per_sec)
            return {
                "running": self.running, "phase": self.phase, "done": self.done, "total": self.total,
                "failed": self.failed, "rate_per_sec": round(self.rate_per_sec, 2), "eta_sec": eta,
                "message": self.message, "error": self.error, "stop_requested": self.stop_requested,
            }


_catalog_cache: dict = {"mtime": None, "size": None, "items": []}


def load_catalog(cfg: SearchConfig) -> list[dict]:
    """读 images.json；按文件 mtime+size 缓存，避免状态轮询时反复解析十几 MB 的 JSON。"""
    p = cfg.data_dir / "images.json"
    if not p.exists():
        return []
    st = p.stat()
    key = (st.st_mtime_ns, st.st_size)
    if _catalog_cache["mtime"] != key:
        _catalog_cache["items"] = json.loads(p.read_text("utf-8")).get("images", [])
        _catalog_cache["mtime"] = key
    return _catalog_cache["items"]


def load_tag_categories(cfg: SearchConfig) -> dict[str, str]:
    p = cfg.data_dir / "structure-tags.json"
    if not p.exists():
        return {}
    tags = json.loads(p.read_text("utf-8")).get("tags", {})
    return {path: (t or {}).get("category") for path, t in tags.items()}


class Indexer:
    def __init__(self, cfg: SearchConfig, store: IndexStore, embedder: Embedder, clip: ClipClassifier):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.clip = clip
        self.progress = Progress()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.store_lock = threading.RLock()  # 与查询共用：flush 与 keep_rows 期间禁止查询

    # ---------- 控制 ----------
    def start(self, rebuild: bool = False, limit: int | None = None) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(rebuild, limit), daemon=True, name="indexer")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._set(stop_requested=True, message="正在停止，保存已认部分…")

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def pending_count(self) -> int:
        """images.json 里有、索引里没有的张数（按 path+size+mtime 快速估算，供状态行显示）。"""
        known = {(it["path"], it["size"], it["mtime_ms"]) for it in self.store.items}
        failed = set(self.store.failed)
        return sum(1 for im in load_catalog(self.cfg) if (im["path"], im["size"], im["mtime_ms"]) not in known and im["path"] not in failed)

    def stale_count(self) -> int:
        """索引里有、images.json 里已经没有的行数（文件被删或文件夹被移除后待清理）。"""
        catalog_paths = {im["path"] for im in load_catalog(self.cfg)}
        return sum(1 for it in self.store.items if it["path"] not in catalog_paths)

    # ---------- 主流程 ----------
    def _set(self, **kw) -> None:
        with self.progress.lock:
            for k, v in kw.items():
                setattr(self.progress, k, v)

    def _run(self, rebuild: bool, limit: int | None) -> None:
        cfg, store = self.cfg, self.store
        try:
            self._set(running=True, phase="scanning", done=0, total=0, failed=0, error="", message="正在对照图库清单…",
                      started_at=time.time(), finished_at=None, rate_per_sec=0.0, stop_requested=False)
            if rebuild:
                with self.store_lock:
                    store.wipe()
            catalog = load_catalog(cfg)
            tag_cats = load_tag_categories(cfg)
            clip_sig = clip_signature(cfg)
            if store.clip_signature and store.clip_signature != clip_sig:
                # CLIP 提示词变了：品类全部重算，但向量不动
                store.categories = {}
            store.clip_signature = clip_sig

            orig_n = len(store.items)
            known_fast = {(it["path"], it["size"], it["mtime_ms"]): i for i, it in enumerate(store.items)}
            catalog_paths = {im["path"] for im in catalog}
            sha_rows = store.sha1_rows()
            todo: list[dict] = []
            present_rows: set[int] = set()
            for im in catalog:
                key = (im["path"], im["size"], im["mtime_ms"])
                if key in known_fast:
                    present_rows.add(known_fast[key])
                    continue
                if im["path"] in store.failed and not rebuild:
                    continue
                todo.append(im)
            if limit:
                todo = todo[:limit]
            self._set(total=len(todo), phase="embedding", message=f"待认 {len(todo)} 张")

            batch_imgs, batch_items, pending_since_flush = [], [], 0
            t0, n_done = time.time(), 0
            last_flush = time.time()

            def flush():
                nonlocal pending_since_flush, last_flush
                with self.store_lock:
                    self._set(phase="saving", message="正在写入索引…")
                    store.save()
                    self._set(phase="embedding")
                pending_since_flush = 0
                last_flush = time.time()

            def process_batch():
                nonlocal n_done
                if not batch_imgs:
                    return
                vecs = self.embedder.embed_library_batch(batch_imgs)
                clip_feats, clip_res = self.clip.classify(batch_imgs)
                for it, r in zip(batch_items, clip_res):
                    tag = tag_cats.get(it["path"])
                    store.categories[it["sha1"]] = {
                        "tag": tag, "clip": r["group"], "prob": round(r["prob"], 3), "margin": round(r["margin"], 3),
                        "groups": merge_groups(cfg, tag, r["group"]),
                    }
                with self.store_lock:
                    store.append(list(batch_items), vecs, clip_feats)
                n_done += len(batch_items)
                batch_imgs.clear()
                batch_items.clear()

            for im in todo:
                if self._stop.is_set():
                    break
                path = im["path"]
                try:
                    sha, data = file_sha1(path)
                except OSError as exc:
                    store.failed[path] = f"读取失败: {exc}"
                    self._set(failed=self.progress.failed + 1)
                    n_done += 1
                    continue
                if sha in sha_rows:
                    row = sha_rows[sha]
                    if row >= len(store.items):
                        # 同一批里出现了内容相同的文件：先把批处理掉，让它落到 store 里
                        process_batch()
                    old = store.items[row]
                    if old["path"] != path and old["path"] in catalog_paths:
                        # 内容相同、原路径也还在库里：是重复文件，复制一行向量，两条路径都能搜到
                        item = {"sha1": sha, "path": path, "size": im["size"], "mtime_ms": im["mtime_ms"]}
                        with self.store_lock:
                            store.append([item], {v: store.vectors[v][row:row + 1] for v in store.variants},
                                         store.clip_vectors[row:row + 1] if store.clip_vectors is not None else None)
                    else:
                        # 原路径已不在库里：是同一文件被移动/改名，只更新路径信息，不重算
                        old.update({"path": path, "size": im["size"], "mtime_ms": im["mtime_ms"]})
                        present_rows.add(row)
                    n_done += 1
                    continue
                try:
                    img = open_image(data, self.embedder.side)
                except ImageDecodeError as exc:
                    store.failed[path] = f"解码失败: {exc}"
                    self._set(failed=self.progress.failed + 1)
                    n_done += 1
                    continue
                item = {"sha1": sha, "path": path, "size": im["size"], "mtime_ms": im["mtime_ms"]}
                batch_imgs.append(img)
                batch_items.append(item)
                sha_rows[sha] = len(store.items) + len(batch_items) - 1
                if len(batch_imgs) >= cfg.batch_size:
                    process_batch()
                    pending_since_flush += cfg.batch_size
                elapsed = max(1e-6, time.time() - t0)
                if not self._stop.is_set():
                    self._set(done=n_done, rate_per_sec=n_done / elapsed, message=f"已认 {n_done} / {len(todo)}")
                # 按张数或按时间落盘，先到者为准：关窗口是硬杀，这里决定最多丢多少
                if pending_since_flush >= cfg.flush_every or (pending_since_flush and time.time() - last_flush >= cfg.flush_seconds):
                    flush()
            process_batch()
            self._set(done=n_done)

            # process_batch 之后 sha_rows 里指向「批内位置」的行号才真正落到 store；
            # 对于批内重复的情况上面已用 process_batch() 先落地，这里不再需要修正。

            # 删除库里已不存在的行（只在没被中断、且不是 --limit 试跑时做，避免误删）。
            # 保留 = 本次在 images.json 里对上的旧行（present_rows）+ 本次新追加的行（>= orig_n）。
            if not self._stop.is_set() and not limit and len(store.items):
                keep = np.array([i in present_rows or i >= orig_n for i in range(len(store.items))], dtype=bool)
                removed = int((~keep).sum())
                if removed:
                    self._set(message=f"清理已不存在的 {removed} 张")
                with self.store_lock:
                    store.keep_rows(keep)
            flush()
            self._set(phase="stopped" if self._stop.is_set() else "done",
                      message="已停止，已认部分已保存" if self._stop.is_set() else "认图完成")
        except Exception as exc:  # noqa: BLE001
            self._set(phase="error", error=f"{type(exc).__name__}: {exc}", message="认图出错，已认部分尝试保存")
            try:
                with self.store_lock:
                    store.save()
            except Exception as save_exc:  # noqa: BLE001
                self._set(error=f"{type(exc).__name__}: {exc}；保存也失败：{type(save_exc).__name__}: {save_exc}")
        finally:
            self._set(running=False, finished_at=time.time(), stop_requested=False)

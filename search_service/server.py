"""本地 HTTP 服务：/health /search /classify /index/start /index/stop /reload-index（design.md §5）。

只监听 127.0.0.1；由 Node 拉起。端口固定，占用即退出并打印中文原因。
查询图只在内存，不落盘；日志只记 sha1 前 8 位与耗时。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from .category import ClipClassifier
from .config import SearchConfig, load_config
from .embedder import Embedder, ImageDecodeError, QueryEmbedding, open_image
from .index_store import IndexStore
from .indexer import Indexer
from .searcher import Searcher


class QueryCache:
    """最近几个查询的图、框、向量，供「改品类重搜」复用（不重传、不重调 GPT）。"""

    def __init__(self, size: int, ttl: int):
        self.size, self.ttl = size, ttl
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()

    def get(self, qid: str) -> dict | None:
        with self.lock:
            e = self.items.get(qid)
            if e and time.time() - e["t"] < self.ttl:
                e["t"] = time.time()
                return e
            self.items.pop(qid, None)
            return None

    def put(self, qid: str, entry: dict) -> None:
        with self.lock:
            entry["t"] = time.time()
            self.items[qid] = entry
            while len(self.items) > self.size:
                oldest = min(self.items, key=lambda k: self.items[k]["t"])
                self.items.pop(oldest)


class App:
    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg
        self.log("正在加载模型…")
        self.embedder = Embedder(cfg)
        self.clip = ClipClassifier(cfg)
        self.store = IndexStore(cfg).load()
        self.indexer = Indexer(cfg, self.store, self.embedder, self.clip)
        self.searcher = Searcher(cfg, self.store, self.embedder, self.clip)
        self.cache = QueryCache(cfg.query_cache_size, cfg.query_cache_ttl_sec)
        self.started = time.time()
        self.log(f"模型就绪：{cfg.embed_model} @ {self.embedder.side}px，设备 {self.embedder.device}，已索引 {len(self.store.items)} 张")

    @staticmethod
    def log(msg: str) -> None:
        try:
            print(f"[search_service] {msg}", flush=True)
        except OSError:
            pass  # Node 已退出、stdout 管道断了：日志写不出去不能影响存盘

    # ---------- 各接口 ----------
    def health(self) -> dict:
        from .indexer import load_catalog

        total = len(load_catalog(self.cfg))
        running = self.indexer.progress.running
        return {
            "ok": True,
            "device": self.embedder.device,
            "model": self.cfg.embed_model,
            "input_size": self.embedder.side,
            "signature": self.store.signature,
            "variants": self.cfg.variants,
            "indexed": len(self.store.items),
            "total": total,
            "pending": self.indexer.pending_count() if not running else None,
            "stale": self.indexer.stale_count() if not running else None,
            "failed": len(self.store.failed),
            "indexing": self.indexer.progress.snapshot(),
            "uptime_sec": int(time.time() - self.started),
        }

    def _decode_query(self, body: dict) -> tuple[str, bytes | None]:
        b64 = body.get("image_b64")
        if not b64:
            return body.get("query_id", ""), None
        if "," in b64 and b64.strip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        raw = base64.b64decode(b64)
        return hashlib.sha1(raw).hexdigest(), raw

    def _prepare(self, body: dict) -> tuple[str, QueryEmbedding, np.ndarray | None, dict]:
        qid, raw = self._decode_query(body)
        box = body.get("box")
        entry = self.cache.get(qid) if qid else None
        if entry and (box is None or entry.get("box_in") == box):
            return qid, entry["emb"], entry.get("clip_feat"), entry
        if raw is None:
            if entry:
                raw = entry["raw"]
            else:
                raise ValueError("query_expired")
        try:
            img = open_image(raw, self.embedder.side)
        except ImageDecodeError as exc:
            raise ValueError(f"decode:{exc}") from exc
        emb = self.embedder.embed_query(img, tuple(box) if box else None)
        clip_feat, clip_res = self.clip.classify([img])
        entry = {"raw": raw, "emb": emb, "clip_feat": clip_feat[0], "clip_res": clip_res[0], "box_in": box}
        self.cache.put(qid, entry)
        return qid, emb, clip_feat[0], entry

    def search(self, body: dict) -> dict:
        t0 = time.time()
        qid, emb, clip_feat, entry = self._prepare(body)
        category = (body.get("category") or "").strip().lower()
        clip_res = entry["clip_res"]
        source = "given"
        uncertain = False
        if not category or category == "auto" or category == "unclear":
            source = "clip"
            if self.clip.is_confident(clip_res):
                category = clip_res["group"]
            else:
                uncertain = True
                category = clip_res["group"]
        group = self.cfg.group_of(category)
        if uncertain:
            return {
                "query_id": qid, "category_used": group, "category_source": source, "category_uncertain": True,
                "clip": clip_res, "results": [], "no_close_match": True, "reason": "category_uncertain",
                "top_score": 0.0, "elapsed_ms": int((time.time() - t0) * 1000),
            }
        with self.indexer.store_lock:
            r = self.searcher.search(emb, group, clip_feat, body.get("limit"))
        self.log(f"search {qid[:8]} group={group} src={source} box={r.box_source} top={r.top_score:.3f} n={len(r.results)} {int((time.time() - t0) * 1000)}ms")
        return {
            "query_id": qid, "category_used": group, "category_source": source, "category_uncertain": False,
            "clip": clip_res, "box_used": r.box_used, "box_source": r.box_source,
            "results": r.results, "no_close_match": r.no_close_match, "reason": r.reason,
            "top_score": r.top_score, "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def classify(self, body: dict) -> dict:
        qid, _, _, entry = self._prepare(body)
        r = entry["clip_res"]
        return {"query_id": qid, "category": r["group"], "prob": r["prob"], "margin": r["margin"], "confident": self.clip.is_confident(r)}


class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, fmt, *args):  # 关掉默认的每请求日志
        return

    def _send(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > self.app.cfg.max_body_bytes:
            raise ValueError("body_too_large")
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw or b"{}")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, self.app.health())
        return self._send(404, {"error": "not_found"})

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/search":
                return self._send(200, self.app.search(body))
            if self.path == "/classify":
                return self._send(200, self.app.classify(body))
            if self.path == "/index/start":
                ok = self.app.indexer.start(rebuild=bool(body.get("rebuild")), limit=body.get("limit"))
                return self._send(200, {"ok": ok, "indexing": self.app.indexer.progress.snapshot()})
            if self.path == "/index/stop":
                self.app.indexer.stop()
                return self._send(200, {"ok": True, "indexing": self.app.indexer.progress.snapshot()})
            if self.path == "/shutdown":
                # Node 退出前调用：停认图、等保存完（最多 body.wait_sec 秒），再由 Node 杀进程
                self.app.indexer.stop()
                self.app.indexer.join(timeout=float(body.get("wait_sec", 20)))
                return self._send(200, {"ok": True, "saved": not self.app.indexer.progress.running})
            if self.path == "/reload-index":
                with self.app.indexer.store_lock:
                    self.app.store = IndexStore(self.app.cfg).load()
                    self.app.indexer.store = self.app.store
                    self.app.searcher.store = self.app.store
                return self._send(200, {"ok": True, "indexed": len(self.app.store.items)})
            return self._send(404, {"error": "not_found"})
        except ValueError as exc:
            msg = str(exc)
            if msg.startswith("decode:"):
                return self._send(400, {"error": "image_decode_failed", "detail": msg[7:]})
            return self._send(400, {"error": msg})
        except Exception as exc:  # noqa: BLE001
            App.log(f"error {type(exc).__name__}: {exc}")
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


class KernelServer(ThreadingHTTPServer):
    # HTTPServer 默认 allow_reuse_address=True；在 Windows 上这等于 SO_REUSEADDR，会让第二个内核
    # 静默地绑到同一个端口，请求被随机分给新旧两个进程。必须关掉，让第二个实例明确报「端口被占用」。
    allow_reuse_address = False


def _watch_parent(app: App, parent_pid: int | None) -> None:
    """Node 没了（关窗口、任务管理器杀掉、崩溃）就把认图停下、存盘、退出，不留孤儿进程占着端口和显存。

    Python 由 Node 以 detached 方式拉起，自己没有控制台，收不到关窗口事件，所以生死只看 Node。
    注意 .venv\\Scripts\\python.exe 在 Windows 上是个转发器，真正的解释器是它的子进程，
    os.getppid() 拿到的是转发器而不是 Node，所以 Node 必须用 --parent-pid 把自己的 PID 传进来。
    """
    pid = parent_pid or os.getppid()
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                App.log(f"无法监视父进程 {pid}（错误 {k32.GetLastError()}），Node 退出后本进程不会自动退出")
                return
            k32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
        else:
            while os.getppid() == pid:
                time.sleep(1)
    except Exception as exc:  # noqa: BLE001
        App.log(f"父进程监视异常：{exc}")
        return
    App.log(f"父进程 {pid} 已退出，停止认图并保存…")
    app.indexer.stop()
    app.indexer.join(timeout=120)
    App.log("已保存，退出")
    os._exit(0)


def main() -> None:
    cfg = load_config()
    port = cfg.port
    parent_pid = None
    for a in sys.argv[1:]:
        if a.startswith("--port="):
            port = int(a.split("=", 1)[1])
        elif a.startswith("--parent-pid="):
            parent_pid = int(a.split("=", 1)[1])
    app = App(cfg)
    Handler.app = app
    try:
        server = KernelServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        App.log(f"端口 {port} 被占用或无法监听：{exc}。请关闭占用该端口的程序，或在 config.json 的 searchService.port 换一个端口。")
        sys.exit(2)
    if parent_pid:
        threading.Thread(target=_watch_parent, args=(app, parent_pid), daemon=True, name="parent-watch").start()
    App.log(f"listening http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Ctrl+C 或 Node 请求关闭：让认图线程停下并把已认部分写盘，再退出
        if app.indexer.progress.running:
            App.log("正在停止认图并保存…")
            app.indexer.stop()
            app.indexer.join(timeout=60)
            App.log("已保存，退出")


if __name__ == "__main__":
    main()

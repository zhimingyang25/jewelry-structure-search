"""公共 CLI 辅助：一次性加载模型与索引，供 bench/smoke/eval/calibrate 复用。"""
from __future__ import annotations

import json
import random
import sys
import time

from .category import ClipClassifier
from .config import SearchConfig, load_config
from .embedder import Embedder
from .index_store import IndexStore
from .indexer import Indexer, load_catalog
from .searcher import Searcher


def parse_args(argv: list[str]) -> dict:
    out: dict = {}
    for a in argv:
        if a.startswith("--"):
            k, _, v = a[2:].partition("=")
            out[k] = v if v != "" else True
    return out


class Ctx:
    def __init__(self, need_index: bool = True):
        self.cfg: SearchConfig = load_config()
        t0 = time.time()
        print(f"加载模型 {self.cfg.embed_model} …", flush=True)
        self.embedder = Embedder(self.cfg)
        self.clip = ClipClassifier(self.cfg)
        self.store = IndexStore(self.cfg).load()
        self.indexer = Indexer(self.cfg, self.store, self.embedder, self.clip)
        self.searcher = Searcher(self.cfg, self.store, self.embedder, self.clip)
        print(f"设备 {self.embedder.device}，输入 {self.embedder.side}px，索引 {len(self.store.items)} 张，用时 {time.time() - t0:.1f}s", flush=True)
        if need_index and not self.store.items:
            print("索引为空。请先在网页点「开始认图」，或运行：py -3.11 -m search_service.build_index --limit=500", flush=True)
            sys.exit(1)

    def catalog(self) -> list[dict]:
        return load_catalog(self.cfg)

    def sample_indexed(self, n: int, seed: int = 0) -> list[dict]:
        rnd = random.Random(seed)
        return rnd.sample(self.store.items, min(n, len(self.store.items)))

    def path_to_row(self) -> dict[str, int]:
        return {it["path"]: i for i, it in enumerate(self.store.items)}


def dump_json(obj, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

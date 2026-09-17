"""预下载模型（安装脚本调用），并自检 GPU。"""
from __future__ import annotations

import sys

from .config import load_config, resolve_model_path


def main() -> None:
    cfg = load_config()
    import torch

    print(f"torch {torch.__version__}  CUDA 可用: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        archs = torch.cuda.get_arch_list()
        name = torch.cuda.get_device_name(0)
        print(f"显卡: {name}  支持架构: {archs}")
        if "sm_61" not in archs:
            print("警告：这个 torch 版本不含 sm_61（Pascal），P104-100 无法使用，将退回 CPU。")
            sys.exit(3)
        x = torch.randn(64, 64, device="cuda") @ torch.randn(64, 64, device="cuda")
        torch.cuda.synchronize()
        print("GPU 计算自检通过")
    from transformers import AutoModel, CLIPModel, CLIPProcessor

    for name in (cfg.embed_model, cfg.clip_model):
        path = resolve_model_path(cfg, name)
        print(f"下载/检查模型 {name} …", flush=True)
        if "clip" in name.lower():
            CLIPModel.from_pretrained(path)
            CLIPProcessor.from_pretrained(path)
        else:
            AutoModel.from_pretrained(path)
        print(f"  {name} 就绪")
    print("模型全部就绪")


if __name__ == "__main__":
    main()

# Jewelry Structure Search

本地珠宝款式比对检索：客人给一张成品图（精修图、实拍图或草图），在几万张犀牛 / JCAD 渲染图里找出同款或大方向一致、可以改的款。

排序靠画面结构相似度（DINOv2 视觉向量，本地 GPU/CPU 计算），品类判断用 OpenAI 兼容视觉 API（可选，没有时用本地 CLIP 粗判）。图库、索引、密钥都只在本机，不随仓库发布。

- 需求基线：`requirements.md`
- 设计与技术拍板：`design.md`，评审回应：`design-review.md`
- 给使用者的大白话说明：`使用说明.md`

## 环境要求

- Windows 10 / 11
- Node.js 18+
- Python 3.11（安装时勾选 Add to PATH；`py -3.11` 能运行即可）
- NVIDIA 显卡可选。有就自动用（Pascal 及以上，脚本会自检），没有则用 CPU，第一次认图会慢很多

## 第一次安装

```text
1. 复制 .env.example 为 .env，填入你的 OpenAI 兼容 API key / base url / 视觉模型名
2. 复制 config.example.json 为 config.json，imageRoot 改成你的图库根目录（也可以之后在网页里扫描）
3. 双击 安装.bat   ← 建 .venv、装依赖、装 torch（先 CPU 保底再试显卡版）、下载约 1 GB 模型
```

国内直连 huggingface.co 失败时脚本会自动改用 hf-mirror.com 重试；也可以在 `.env` 里写 `HF_ENDPOINT=https://hf-mirror.com`。模型下载过一次后，之后启动完全离线加载。

## 日常使用

```text
双击 启动.bat → 浏览器打开 http://localhost:8787
```

1. 顶部「图库文件夹」登记图片所在的根目录（可多个）；「重新扫描全部」把这些目录在硬盘上的真实状态同步进 `data/images.json`（新增、删除、变动）
2. 顶部「开始认图」给全库算向量（每批自动保存，可随时停、关窗口也不丢；P104-100 上约每秒 2～3 张，5 万张约 6 小时）
3. 左侧上传客人图，点「找同款」；结果按相似度从高到低，最多 100 张
4. 顶部橙色「没找到很像的同款」表示最高相似度低于阈值，下面是类似可改的款
5. 品类判错时在下拉里改，点「按这个品类重搜」，不用重新上传
6. 点「打开所在位置」在资源管理器里定位到这张图

## 命令行工具（可选）

```powershell
npm run scan     # 命令行重扫全部已登记目录（config.imageRoot 未登记时顺带加入）
npm run index    # 命令行认图（等价于网页按钮，方便过夜跑）
npm run bench    # 抽 200 张实测速度并外推全库耗时
npm run smoke    # 随机 20 张库图查自己，验证管线
npm run eval     # 用 eval/pairs.json 里的真实对子评测名次（格式见 search_service/eval.py 顶部）
```

## 目录

```text
src/              Node 服务：静态网页、/api/search、拉起 Python 内核、打开文件夹
search_service/   Python 检索内核：DINOv2 向量、CLIP 品类、索引、查询、评测工具
web/              网页
data/             本机数据（images.json、index/、structure-tags.json）— 不入库
```

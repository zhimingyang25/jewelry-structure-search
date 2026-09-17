# Jewelry Structure Search

本地珠宝结构索引原型。真实图库目录请在本机配置文件中设置，图片数据不随仓库发布。

## 1. 启动网页

```powershell
npm run scan
npm run start
```

然后打开：

```text
http://localhost:8787
```

## 2. 配置 OpenAI API key

复制 `.env.example` 为 `.env`，然后把里面的 key 换成你的真实 key：

```text
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
OPENAI_API_MODE=chat
```

也可以只在当前 PowerShell 窗口临时设置：

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
$env:OPENAI_MODEL="gpt-4.1-mini"
$env:OPENAI_API_MODE="chat"
```

如果使用 New API 这类 OpenAI 兼容中转服务，把 `OPENAI_BASE_URL` 改成你的 New API 地址，通常格式类似：

```text
OPENAI_API_KEY=your-newapi-token
OPENAI_BASE_URL=http://你的NewAPI地址/v1
OPENAI_MODEL=你的NewAPI里可用的视觉模型名
OPENAI_API_MODE=chat
```

`OPENAI_API_MODE` 默认建议用 `chat`，因为大多数 New API 兼容的是 `/v1/chat/completions`。如果你直连官方 Responses API，再改成：

```text
OPENAI_API_MODE=responses
```

## 3. 批量给图库打结构标签

建议先小批量测试：

```powershell
npm run tag:openai -- --limit 20
```

确认标签效果不错后再扩大：

```powershell
npm run repair:tags
npm run compact:tags
npm run tag:openai -- --limit 1000
```

生成的标签会写入：

```text
data\structure-tags.json
```

## 4. 重要说明

当前版本不需要安装额外 npm 包，只依赖电脑已有的 Node.js。它支持 OpenAI 兼容的 `chat/completions` 和官方 `responses` 两种模式，搜索时优先匹配珠宝结构标签，而不是只看普通图片相似度。

## 5. 大图处理

如果图库里有很大的图片，New API 可能返回 `413 Request Entity Too Large`。项目支持安装 `sharp` 后自动把大图缩到适合上传的 JPG：

```powershell
npm install
```

可在 `.env` 调整压缩参数：

```text
MAX_IMAGE_BYTES=4194304
MAX_IMAGE_SIDE=1600
IMAGE_JPEG_QUALITY=82
```

没有安装 `sharp` 时，超大图片会被跳过并记录到 `data\tag-failures.json`，不会影响后续批量打标签。

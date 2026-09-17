import childProcess from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { dataPath, loadConfig, loadEnv, mimeFromPath, readJson, writeJson } from "./common.mjs";
import { detectCategoryAndBox } from "./category-client.mjs";
import { mergeCatalogs, scanImages } from "./scanner.mjs";

loadEnv();
let config = loadConfig();
let catalog = readJson(dataPath(config, "images.json"), { images: [] });
let byId = new Map(catalog.images.map((image) => [String(image.id), image]));
let byPath = new Map(catalog.images.map((image) => [image.path, image]));
const env = { ...(globalThis.__LOCAL_ENV__ || {}), ...(globalThis.process?.env || {}) };
const requestedPort = Number(env.PORT || config.port || 8787);
let currentPort = requestedPort;

const search = config.searchService || {};
const SEARCH_PORT = Number(search.port || 8788);
const SEARCH_URL = `http://127.0.0.1:${SEARCH_PORT}`;
const GPT_TIMEOUT = Number(search.gptTimeoutMs || 12000);
const SEARCH_TIMEOUT = Number(search.searchTimeoutMs || 60000);

// 品类组（与 search_service/config.py 的默认表一致；前端下拉用中文名）
const CATEGORY_LABELS = {
  ring: "戒指",
  pendant: "吊坠/项链",
  earring: "耳环",
  bracelet: "手链",
  bangle: "手镯",
  brooch: "胸针",
  other: "其它"
};

// ---------- Python 检索服务：由 Node 拉起并随 Node 退出 ----------
let pythonProc = null;
let pythonState = { running: false, lastError: "", startedAt: null };

function pythonExe() {
  return path.resolve(search.python || ".venv/Scripts/python.exe");
}

function startPython() {
  const exe = pythonExe();
  if (!fs.existsSync(exe)) {
    pythonState = { running: false, lastError: "还没安装检索内核，请先双击 安装.bat", startedAt: null };
    console.log(`[search] ${pythonState.lastError}`);
    return;
  }
  pythonProc = childProcess.spawn(exe, ["-m", "search_service.server", `--port=${SEARCH_PORT}`], {
    cwd: path.resolve("."),
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    env: { ...globalThis.process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" }
  });
  pythonState = { running: true, lastError: "", startedAt: Date.now() };
  pythonProc.stdout.on("data", (chunk) => globalThis.process.stdout.write(chunk));
  pythonProc.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    globalThis.process.stderr.write(text);
    if (/Error|错误|Traceback/.test(text)) pythonState.lastError = text.split("\n").filter(Boolean).slice(-1)[0] || "";
  });
  pythonProc.on("exit", (code) => {
    pythonState.running = false;
    if (code === 2) pythonState.lastError = `检索内核端口 ${SEARCH_PORT} 被占用，请关闭占用程序或改 config.json 的 searchService.port`;
    else if (code) pythonState.lastError = pythonState.lastError || `检索内核退出，代码 ${code}`;
    console.log(`[search] python exited with code ${code}`);
  });
}

function stopPython() {
  if (pythonProc && !pythonProc.killed) {
    try {
      pythonProc.kill();
    } catch {
      /* ignore */
    }
  }
}
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  globalThis.process.on(signal, () => {
    stopPython();
    globalThis.process.exit(0);
  });
}
globalThis.process.on("exit", stopPython);

async function pyFetch(route, body, timeoutMs = SEARCH_TIMEOUT) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${SEARCH_URL}${route}`, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(data.error || `search service ${response.status}`);
      err.detail = data.detail;
      err.status = response.status;
      throw err;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function pyHealth() {
  try {
    return await pyFetch("/health", undefined, 3000);
  } catch {
    return null;
  }
}

// ---------- 通用 ----------
function writeCatalog(nextCatalog) {
  fs.mkdirSync(path.dirname(dataPath(config, "images.json")), { recursive: true });
  fs.writeFileSync(dataPath(config, "images.json"), JSON.stringify(nextCatalog, null, 2), "utf8");
  catalog = nextCatalog;
  byId = new Map(catalog.images.map((image) => [String(image.id), image]));
  byPath = new Map(catalog.images.map((image) => [image.path, image]));
}

function saveConfig(nextConfig) {
  config = nextConfig;
  writeJson(path.resolve("config.json"), config);
}

function sendJson(res, data, status = 200) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(data));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 20 * 1024 * 1024) req.destroy(new Error("Body too large"));
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

function serveStatic(req, res, pathname) {
  const filePath = path.resolve("web", pathname === "/" ? "index.html" : pathname.slice(1));
  if (!filePath.startsWith(path.resolve("web"))) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    res.writeHead(404);
    res.end("Not found");
    return;
  }
  const ext = path.extname(filePath).toLowerCase();
  const type = ext === ".css" ? "text/css" : ext === ".js" ? "text/javascript" : "text/html";
  res.writeHead(200, { "Content-Type": `${type}; charset=utf-8` });
  fs.createReadStream(filePath).pipe(res);
}

// ---------- 状态 ----------
async function buildStatus() {
  const health = await pyHealth();
  const hasKey = Boolean(env.OPENAI_API_KEY);
  const total = catalog.count || catalog.images.length;
  const index = health
    ? {
        ready: health.indexed > 0,
        indexed: health.indexed,
        total,
        pending: health.pending ?? Math.max(0, total - health.indexed),
        failed: health.failed,
        model: health.model,
        inputSize: health.input_size,
        device: health.device,
        signature: health.signature,
        indexing: health.indexing
      }
    : { ready: false, indexed: 0, total, pending: total, failed: 0, indexing: { running: false } };
  return {
    imageRoot: catalog.imageRoot || config.imageRoot,
    imageRoots: catalog.imageRoots || (catalog.imageRoot ? [catalog.imageRoot] : []),
    count: total,
    hasOpenAiKey: hasKey,
    categories: CATEGORY_LABELS,
    services: {
      python: health ? "ok" : pythonState.running ? "starting" : "down",
      pythonError: health ? "" : pythonState.lastError,
      gpt: hasKey ? "configured" : "missing_key"
    },
    index
  };
}

// ---------- 搜索 ----------
async function handleSearch(body) {
  const t0 = Date.now();
  const limit = Math.min(Number(body.limit || config.topK || 100), 300);
  const manualCategory = String(body.category || "").trim().toLowerCase();
  const imageDataUrl = body.imageDataUrl || "";
  if (imageDataUrl && !/^data:image\/(jpeg|jpg|png|gif|webp);base64,/i.test(imageDataUrl)) {
    return { error: "图片格式不支持，请用 JPG 或 PNG。", status: 400 };
  }
  if (!imageDataUrl && !body.query_id) {
    return { error: "请先选择客人图。", status: 400 };
  }

  // 1. GPT：品类 + 主体框。用户手动指定品类时也要框（busy 图裁剪），除非缓存里已有。
  let detected = { category: "unclear", box: null, confidence: 0, source: "none" };
  let gptError = "";
  if (imageDataUrl && env.OPENAI_API_KEY) {
    try {
      detected = await detectCategoryAndBox({ imageDataUrl, timeoutMs: GPT_TIMEOUT });
    } catch (error) {
      gptError = error.name === "AbortError" ? "视觉识别超时" : error.message;
    }
  } else if (imageDataUrl && !env.OPENAI_API_KEY) {
    gptError = "未配置 OPENAI_API_KEY";
  }

  // 2. Python：向量 + 过滤 + 排序
  const payload = {
    query_id: body.query_id || undefined,
    image_b64: imageDataUrl ? imageDataUrl.split(",", 2)[1] : undefined,
    box: detected.box || body.box || undefined,
    category: manualCategory || (detected.category !== "unclear" ? detected.category : undefined),
    limit
  };
  let result;
  try {
    result = await pyFetch("/search", payload);
  } catch (error) {
    if (error.message === "query_expired") return { error: "这张图的缓存已过期，请重新选择图片再搜。", status: 400 };
    if (error.message === "image_decode_failed") return { error: "这张图打不开，请另存为 JPG 后再试。", status: 400 };
    if (error.name === "AbortError") return { error: "检索超时，请稍后再试。", status: 504 };
    return { error: `检索内核未响应：${error.message}。请确认 启动.bat 窗口没有报错。`, status: 503 };
  }

  const categorySource = manualCategory ? "manual" : detected.category !== "unclear" ? "gpt" : result.category_source;
  const results = result.results
    .map((item) => {
      const image = byPath.get(item.path);
      if (!image) return null;
      return { ...image, score: item.score, sha1: item.sha1 };
    })
    .filter(Boolean);

  return {
    query_id: result.query_id,
    detected_category: detected.category,
    category_used: result.category_used,
    category_label: CATEGORY_LABELS[result.category_used] || result.category_used,
    category_source: categorySource,
    category_uncertain: Boolean(result.category_uncertain),
    box_source: result.box_source || "none",
    gpt_error: gptError,
    no_close_match: Boolean(result.no_close_match),
    reason: result.reason,
    top_score: result.top_score,
    results,
    elapsed_ms: Date.now() - t0
  };
}

// ---------- HTTP ----------
const server = http.createServer(async (req, res) => {
  try {
    const activePort = server.address()?.port || currentPort;
    const url = new URL(req.url, `http://localhost:${activePort}`);

    if (url.pathname === "/api/status") {
      sendJson(res, await buildStatus());
      return;
    }
    if (url.pathname === "/api/images") {
      const offset = Number(url.searchParams.get("offset") || 0);
      const limit = Math.min(Number(url.searchParams.get("limit") || 80), 300);
      const q = (url.searchParams.get("q") || "").toLowerCase().trim();
      const source = q
        ? catalog.images.filter((image) => `${image.file_name} ${image.folder} ${image.relative_path}`.toLowerCase().includes(q))
        : catalog.images;
      sendJson(res, { total: source.length, images: source.slice(offset, offset + limit) });
      return;
    }
    if (url.pathname === "/api/scan" && req.method === "POST") {
      const bodyText = await readBody(req);
      const body = bodyText ? JSON.parse(bodyText) : {};
      const previousCount = catalog.images?.length || 0;
      const requestedRoot = String(body.imageRoot || "").trim();
      const append = body.append !== false;
      if (requestedRoot) {
        if (!fs.existsSync(requestedRoot) || !fs.statSync(requestedRoot).isDirectory()) {
          sendJson(res, { error: `扫描目录不存在或不是文件夹：${requestedRoot}` }, 400);
          return;
        }
        saveConfig({ ...config, imageRoot: requestedRoot });
      }
      const scannedCatalog = scanImages(config);
      const nextCatalog = append ? mergeCatalogs(catalog, scannedCatalog) : scannedCatalog;
      writeCatalog(nextCatalog);
      const status = await buildStatus();
      sendJson(res, {
        ok: true,
        previousCount,
        count: nextCatalog.count,
        added: Math.max(0, nextCatalog.count - previousCount),
        imageRoot: nextCatalog.imageRoot,
        imageRoots: nextCatalog.imageRoots || (nextCatalog.imageRoot ? [nextCatalog.imageRoot] : []),
        scannedAt: nextCatalog.scannedAt,
        index: status.index
      });
      return;
    }
    if (url.pathname.startsWith("/asset/")) {
      const image = byId.get(url.pathname.split("/").pop());
      if (!image || !fs.existsSync(image.path)) {
        res.writeHead(404);
        res.end("Image not found");
        return;
      }
      res.writeHead(200, { "Content-Type": mimeFromPath(image.path), "Cache-Control": "public, max-age=3600" });
      fs.createReadStream(image.path).pipe(res);
      return;
    }
    if (url.pathname === "/api/search" && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      const data = await handleSearch(body);
      if (data.error) {
        sendJson(res, { error: data.error }, data.status || 500);
        return;
      }
      sendJson(res, data);
      return;
    }
    if (url.pathname === "/api/index/start" && req.method === "POST") {
      const bodyText = await readBody(req);
      const body = bodyText ? JSON.parse(bodyText) : {};
      try {
        sendJson(res, await pyFetch("/index/start", { rebuild: Boolean(body.rebuild) }, 10000));
      } catch (error) {
        sendJson(res, { error: `检索内核未响应：${error.message}` }, 503);
      }
      return;
    }
    if (url.pathname === "/api/index/stop" && req.method === "POST") {
      try {
        sendJson(res, await pyFetch("/index/stop", {}, 10000));
      } catch (error) {
        sendJson(res, { error: `检索内核未响应：${error.message}` }, 503);
      }
      return;
    }
    if (url.pathname === "/api/open-folder" && req.method === "POST") {
      const body = JSON.parse(await readBody(req));
      const image = byId.get(String(body.id));
      if (!image) {
        sendJson(res, { error: "找不到这张图的记录" }, 404);
        return;
      }
      if (!fs.existsSync(image.path)) {
        sendJson(res, { error: `文件已不在原位置：${image.path}` }, 410);
        return;
      }
      childProcess.spawn("explorer.exe", ["/select,", image.path], { detached: true, stdio: "ignore" }).unref();
      sendJson(res, { ok: true });
      return;
    }
    serveStatic(req, res, url.pathname);
  } catch (error) {
    sendJson(res, { error: error.message }, 500);
  }
});

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    currentPort += 1;
    console.log(`Port is busy, retrying on http://localhost:${currentPort}`);
    server.listen(currentPort);
    return;
  }
  throw error;
});

server.listen(requestedPort, () => {
  const activePort = server.address()?.port || currentPort;
  console.log(`珠宝同款检索: http://localhost:${activePort}`);
  console.log("服务运行中。请在浏览器打开上面的地址，并保持本窗口开着。按 Ctrl+C 停止。");
  startPython();
});

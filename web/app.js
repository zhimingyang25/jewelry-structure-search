const $ = (sel) => document.querySelector(sel);
const grid = $("#grid");
const statusEl = $("#status");
const indexText = $("#indexText");
const indexStartBtn = $("#indexStartBtn");
const indexStopBtn = $("#indexStopBtn");
const serviceNote = $("#serviceNote");
const countEl = $("#count");
const upload = $("#upload");
const preview = $("#preview");
const searchBtn = $("#searchBtn");
const researchBtn = $("#researchBtn");
const categorySel = $("#category");
const categoryNote = $("#categoryNote");
const actionStatus = $("#actionStatus");
const notice = $("#notice");
const scanBtn = $("#scanBtn");
const scanRoot = $("#scanRoot");

const state = {
  dataUrl: "",
  queryId: "",
  status: null,
  busy: false,
  pollTimer: null
};

function setActionStatus(message, tone = "") {
  actionStatus.textContent = message || "";
  actionStatus.className = `actionStatus${tone ? ` ${tone}` : ""}`;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("读取图片失败。"));
    reader.readAsDataURL(file);
  });
}

function convertToJpeg(dataUrl, maxSide = 1600, quality = 0.9) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const w = image.naturalWidth || image.width;
      const h = image.naturalHeight || image.height;
      const scale = Math.min(1, maxSide / Math.max(w, h));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(w * scale));
      canvas.height = Math.max(1, Math.round(h * scale));
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL("image/jpeg", quality));
    };
    image.onerror = () => reject(new Error("这张图浏览器无法读取，请另存为 JPG 或 PNG 再试。"));
    image.src = dataUrl;
  });
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

// ---------- 状态栏 ----------
function fillCategories(labels) {
  if (categorySel.options.length > 1) return;
  for (const [key, label] of Object.entries(labels || {})) {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = label;
    categorySel.appendChild(opt);
  }
}

function renderStatus(s) {
  state.status = s;
  fillCategories(s.categories);
  const idx = s.index || {};
  const devText = idx.device ? `${idx.model || ""}@${idx.inputSize || ""}px，${idx.device === "cuda" ? "显卡" : "CPU"}` : "";
  statusEl.textContent = `图库 ${s.count} 张｜目录：${(s.imageRoots || []).length} 个`;
  const parts = [`已认 ${idx.indexed ?? 0} / ${idx.total ?? s.count}`];
  if (idx.pending > 0) parts.push(`待认 ${idx.pending}`);
  if (idx.failed > 0) parts.push(`失败 ${idx.failed}`);
  if (devText) parts.push(devText);
  const ing = idx.indexing || {};
  if (ing.running) {
    const eta = ing.eta_sec != null ? `，预计还需 ${Math.max(1, Math.round(ing.eta_sec / 60))} 分钟` : "";
    parts.push(`认图中：${ing.done}/${ing.total}（${ing.rate_per_sec || 0} 张/秒${eta}）`);
  } else if (ing.phase === "error") {
    parts.push(`认图出错：${ing.error}`);
  } else if (ing.phase === "stopped") {
    parts.push("认图已停止，已认部分已保存");
  }
  indexText.textContent = parts.join("｜");

  const pyOk = s.services?.python === "ok";
  indexStartBtn.hidden = !pyOk || ing.running || !(idx.pending > 0 || idx.indexed === 0);
  indexStartBtn.textContent = idx.indexed === 0 ? "开始认图（第一次会很久）" : `认新图（${idx.pending} 张）`;
  indexStopBtn.hidden = !ing.running;

  let note = "";
  if (s.services?.python === "down") note = s.services.pythonError || "检索内核没有启动。请看 启动.bat 窗口里的报错。";
  else if (s.services?.python === "starting") note = "检索内核正在启动、加载模型，请稍等几秒…";
  else if (!idx.ready) note = "还没有认过图。点上面的「开始认图」，认完才能搜。";
  else if (s.services?.gpt !== "configured") note = "未配置视觉 API（.env 里的 OPENAI_API_KEY），品类会用本地粗判，认错请手动选。";
  serviceNote.textContent = note;

  const canSearch = pyOk && idx.ready && Boolean(state.dataUrl) && !state.busy;
  searchBtn.disabled = !canSearch;
  researchBtn.disabled = !(pyOk && idx.ready && state.queryId && !state.busy);

  if (ing.running || s.services?.python === "starting") schedulePoll(3000);
  else schedulePoll(15000);
}

function schedulePoll(ms) {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(refreshStatus, ms);
}

async function refreshStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    statusEl.textContent = `连接服务失败：${error.message}`;
    schedulePoll(5000);
  }
}

// ---------- 结果 ----------
function showNotice(kind, text, sub = "") {
  notice.hidden = false;
  notice.className = `notice ${kind}`;
  notice.innerHTML = `${text}${sub ? `<small>${sub}</small>` : ""}`;
}

function renderResults(images) {
  countEl.textContent = `${images.length} 张`;
  grid.innerHTML = images.map((image, i) => `
    <article class="card">
      <span class="rank">${i + 1}</span>
      <img src="/asset/${image.id}" loading="lazy" alt="">
      <div class="cardBody">
        <div class="name" title="${image.path}">${image.file_name}</div>
        <div class="meta">${image.folder || "."}</div>
        <div class="score">相似度 ${Number(image.score).toFixed(3)}</div>
        <button class="openBtn" data-id="${image.id}">打开所在位置</button>
        <div class="openErr"></div>
      </div>
    </article>
  `).join("");
}

function describeCategory(data) {
  const label = data.category_label || data.category_used;
  const src = { gpt: "视觉 API 判断", manual: "你指定的", clip: "本地粗判" }[data.category_source] || data.category_source;
  let text = `系统认为是：${label}（${src}）`;
  if (data.gpt_error) text += `｜视觉 API 未成功：${data.gpt_error}`;
  return text;
}

async function runSearch({ reuse }) {
  if (state.busy) return;
  const manual = categorySel.value !== "auto" ? categorySel.value : "";
  const body = reuse && state.queryId ? { query_id: state.queryId, category: manual } : { imageDataUrl: state.dataUrl, category: manual };
  if (!body.imageDataUrl && !body.query_id) {
    setActionStatus("请先选择客人图。", "error");
    return;
  }
  state.busy = true;
  searchBtn.disabled = researchBtn.disabled = true;
  setActionStatus(reuse ? "正在按指定品类重搜…" : "正在识别品类并比对全库，通常十几秒…");
  notice.hidden = true;
  try {
    let data;
    try {
      data = await post("/api/search", body);
    } catch (error) {
      if (reuse && /过期/.test(error.message) && state.dataUrl) {
        data = await post("/api/search", { imageDataUrl: state.dataUrl, category: manual });
      } else {
        throw error;
      }
    }
    state.queryId = data.query_id || state.queryId;
    if (!manual && data.category_used && [...categorySel.options].some((o) => o.value === data.category_used)) {
      categorySel.value = data.category_used;
    }
    categoryNote.textContent = describeCategory(data);
    if (data.category_uncertain) {
      renderResults([]);
      showNotice("warn", "没看出这是什么品类，请在左边选一个品类，再点「按这个品类重搜」。");
      setActionStatus("等待你选择品类。");
      return;
    }
    renderResults(data.results || []);
    if (data.no_close_match) {
      showNotice("warn", "没找到很像的同款，下面是类似、可改的款。", `最高相似度 ${Number(data.top_score).toFixed(3)}｜只显示「${data.category_label}」`);
    } else {
      showNotice("info", `最高相似度 ${Number(data.top_score).toFixed(3)}`, `只显示「${data.category_label}」｜系统不判断同款，请自行看图`);
    }
    setActionStatus(`完成，${data.results.length} 张，用时 ${(data.elapsed_ms / 1000).toFixed(1)} 秒。品类不对就改左边下拉再重搜。`, "success");
  } catch (error) {
    setActionStatus(error.message, "error");
  } finally {
    state.busy = false;
    refreshStatus();
  }
}

// ---------- 事件 ----------
upload.addEventListener("change", async () => {
  const file = upload.files?.[0];
  if (!file) return;
  try {
    setActionStatus("正在读取客人图…");
    const original = await readFileAsDataUrl(file);
    state.dataUrl = await convertToJpeg(original);
    state.queryId = "";
    categorySel.value = "auto";
    categoryNote.textContent = "";
    notice.hidden = true;
    preview.src = state.dataUrl;
    preview.style.display = "block";
    setActionStatus("客人图已就绪，点「找同款」。", "success");
  } catch (error) {
    state.dataUrl = "";
    preview.removeAttribute("src");
    preview.style.display = "none";
    setActionStatus(error.message, "error");
  }
  refreshStatus();
});

searchBtn.addEventListener("click", () => runSearch({ reuse: false }));
researchBtn.addEventListener("click", () => runSearch({ reuse: true }));
categorySel.addEventListener("change", () => {
  if (state.queryId) setActionStatus("已改品类，点「按这个品类重搜」生效。");
});

indexStartBtn.addEventListener("click", async () => {
  try {
    indexStartBtn.disabled = true;
    await post("/api/index/start", {});
    setActionStatus("已开始认图。可以一直开着，第一次可能要几小时；中途关掉下次会接着认。", "success");
  } catch (error) {
    setActionStatus(error.message, "error");
  } finally {
    indexStartBtn.disabled = false;
    refreshStatus();
  }
});

indexStopBtn.addEventListener("click", async () => {
  try {
    await post("/api/index/stop", {});
    setActionStatus("正在停止认图，已认部分会保存。");
  } catch (error) {
    setActionStatus(error.message, "error");
  }
  refreshStatus();
});

scanBtn.addEventListener("click", async () => {
  try {
    scanBtn.disabled = true;
    const imageRoot = scanRoot.value.trim();
    if (!imageRoot) {
      setActionStatus("请先填写要扫描的图库文件夹路径。", "error");
      return;
    }
    setActionStatus("正在扫描图库…");
    const result = await post("/api/scan", { imageRoot });
    scanRoot.value = result.imageRoot || imageRoot;
    const pending = result.index?.pending ?? result.added;
    setActionStatus(`扫描完成：当前 ${result.count} 张，比上次多 ${result.added} 张。${pending > 0 ? `有 ${pending} 张还没认，点上面「认新图」。` : ""}`, "success");
  } catch (error) {
    setActionStatus(error.message, "error");
  } finally {
    scanBtn.disabled = false;
    refreshStatus();
  }
});

grid.addEventListener("click", async (event) => {
  const button = event.target.closest(".openBtn");
  if (!button) return;
  const errEl = button.parentElement.querySelector(".openErr");
  errEl.textContent = "";
  try {
    await post("/api/open-folder", { id: button.dataset.id });
  } catch (error) {
    errEl.textContent = error.message;
  }
});

refreshStatus().then(() => {
  if (state.status?.imageRoot) scanRoot.value = state.status.imageRoot;
});

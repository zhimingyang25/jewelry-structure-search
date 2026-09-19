// 复现用户场景：内核已加载现有索引 → 认新图 → 中途点停止 → 关掉重开 → 数量是否保留。
// 直接打 Python 内核端口（可传 limit）。用法：node scripts/test-index-persist.mjs [pyPort]
const pyPort = process.argv[2] || "8788";
const py = `http://127.0.0.1:${pyPort}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function j(path, body) {
  const res = await fetch(`${py}${path}`, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return res.json();
}

let h;
for (let i = 0; i < 60; i++) {
  try { h = await j("/health"); if (h.ok) break; } catch { /* not up */ }
  await sleep(2000);
}
if (!h?.ok) { console.log("kernel not reachable on", pyPort); process.exit(1); }
if (h.stale === undefined) { console.log("WARNING: this kernel is running OLD code (no `stale` field) — test would be meaningless"); process.exit(2); }
console.log(`start: indexed=${h.indexed} pending=${h.pending} stale=${h.stale} device=${h.device}`);
const before = h.indexed;

// 1) 认 60 张到完成，看速度、落盘
let r = await j("/index/start", { limit: 60 });
console.log("start(limit=60) ->", r.ok);
let last = -1;
const t1 = Date.now();
for (let i = 0; i < 600; i++) {
  await sleep(1000);
  h = await j("/health");
  const s = h.indexing;
  if (s.done !== last) { console.log(`  ${String(Math.round((Date.now() - t1) / 1000)).padStart(4)}s ${s.phase} ${s.done}/${s.total} ${s.rate_per_sec}/s ${s.message}`); last = s.done; }
  if (!s.running) break;
}
console.log(`run#1 done in ${Math.round((Date.now() - t1) / 1000)}s: phase=${h.indexing.phase} error="${h.indexing.error}" indexed=${h.indexed} (was ${before})`);
const afterRun1 = h.indexed;

// 2) 再认 300 张，跑 6 秒后点停止：看停止响应时间与保存
r = await j("/index/start", { limit: 300 });
await sleep(6000);
const tStop = Date.now();
r = await j("/index/stop", {});
console.log(`stop requested -> stop_requested=${r.indexing?.stop_requested} at done=${r.indexing?.done}`);
for (let i = 0; i < 240; i++) {
  await sleep(500);
  h = await j("/health");
  if (!h.indexing.running) break;
}
console.log(`stopped after ${Date.now() - tStop} ms: phase=${h.indexing.phase} done=${h.indexing.done} error="${h.indexing.error}" indexed=${h.indexed}`);
const afterStop = h.indexed;
console.log(JSON.stringify({ before, afterRun1, afterStop }));

// 端到端冒烟：等内核就绪 → /api/status → /api/search（自动品类，走 GPT）→ 改品类重搜（query_id）→ 错误分支。
// 用法：node scripts/e2e.mjs [port]
import fs from "node:fs";

const port = process.argv[2] || "8787";
const base = `http://localhost:${port}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function j(path, body) {
  const res = await fetch(`${base}${path}`, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, data };
}

// 1. 等 Python 内核就绪（最多 90 秒）
let status;
for (let i = 0; i < 45; i++) {
  ({ data: status } = await j("/api/status"));
  if (status.services?.python === "ok") break;
  await sleep(2000);
}
console.log("status:", JSON.stringify({ python: status.services?.python, gpt: status.services?.gpt, err: status.services?.pythonError, index: status.index }, null, 0));
if (status.services?.python !== "ok") { console.log("FAIL: python kernel not ready"); process.exit(1); }

// 2. 拿一张已索引的图当客人图
const meta = JSON.parse(fs.readFileSync(`data/index/${status.index.signature}/meta.json`, "utf8"));
const target = meta.items[0];
const bytes = fs.readFileSync(target.path);
const dataUrl = `data:image/jpeg;base64,${bytes.toString("base64")}`;
console.log("query image:", target.path.split("\\").pop(), `${Math.round(bytes.length / 1024)} KB`);

// 3. 自动品类搜索（会调 GPT）
let t = Date.now();
const s1 = await j("/api/search", { imageDataUrl: dataUrl });
console.log(`search#1 ${s1.status} ${Date.now() - t}ms:`, JSON.stringify({
  detected: s1.data.detected_category, used: s1.data.category_used, src: s1.data.category_source, box: s1.data.box_source,
  gpt_error: s1.data.gpt_error, no_close: s1.data.no_close_match, top: s1.data.top_score, n: s1.data.results?.length,
  first: s1.data.results?.[0]?.file_name, firstScore: s1.data.results?.[0]?.score, error: s1.data.error
}));
const selfRank = (s1.data.results || []).findIndex((r) => r.path === target.path) + 1;
console.log("self rank:", selfRank || "not found");

// 4. 改品类重搜：只传 query_id，不重传图，不再调 GPT
t = Date.now();
const s2 = await j("/api/search", { query_id: s1.data.query_id, category: "pendant" });
console.log(`search#2 (manual pendant, reuse) ${s2.status} ${Date.now() - t}ms:`, JSON.stringify({
  used: s2.data.category_used, src: s2.data.category_source, no_close: s2.data.no_close_match, n: s2.data.results?.length, error: s2.data.error
}));

// 5. 同品类重搜（ring）应与 #1 结果一致
const s3 = await j("/api/search", { query_id: s1.data.query_id, category: "ring" });
console.log(`search#3 (manual ring, reuse) ${s3.status}:`, JSON.stringify({ n: s3.data.results?.length, first: s3.data.results?.[0]?.file_name, src: s3.data.category_source }));

// 6. 错误分支
const e1 = await j("/api/search", { query_id: "deadbeef".repeat(5) });
console.log("expired query_id ->", e1.status, e1.data.error);
const e2 = await j("/api/search", { imageDataUrl: "data:image/jpeg;base64,AAAA" });
console.log("garbage image ->", e2.status, e2.data.error);
const e3 = await j("/api/open-folder", { id: "999999999" });
console.log("open-folder bad id ->", e3.status, e3.data.error);
const e4 = await j("/api/search", {});
console.log("empty body ->", e4.status, e4.data.error);

// 7. 图库文件夹：状态里有 roots；重扫全部是幂等的；新增不存在的目录报 400
const rootsOk = Array.isArray(status.roots) && status.roots.length > 0 && status.roots.every((r) => typeof r.count === "number" && typeof r.exists === "boolean");
console.log("status.roots:", rootsOk ? `${status.roots.length} roots, ${status.roots.reduce((n, r) => n + r.count, 0)} images` : "MISSING/BAD");
t = Date.now();
const sc = await j("/api/scan", {});
console.log(`rescan all ${sc.status} ${Date.now() - t}ms:`, JSON.stringify({ count: sc.data.count, added: sc.data.added, removed: sc.data.removed, changed: sc.data.changed, skipped: sc.data.skippedRoots?.length, pending: sc.data.index?.pending }));
const e5 = await j("/api/scan", { addRoot: "Q:\\definitely-missing" });
console.log("add missing root ->", e5.status, e5.data.error);
const e6 = await j("/api/roots/remove", {});
console.log("remove without root ->", e6.status, e6.data.error);

const ok = s1.status === 200 && selfRank === 1 && s2.status === 200 && s2.data.results.length === 0 && s3.status === 200
  && e1.status === 400 && e2.status === 400 && e3.status === 404 && e4.status === 400
  && rootsOk && sc.status === 200 && sc.data.count === status.count && e5.status === 400 && e6.status === 400;
console.log(ok ? "E2E PASS" : "E2E FAIL");
process.exit(ok ? 0 : 1);

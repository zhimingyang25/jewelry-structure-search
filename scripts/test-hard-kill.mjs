// 最坏情况：认图进行中，用户直接关掉窗口（= 硬杀 Node）。
// 期望：Python 内核发现父进程没了 → 停认图 → 存盘 → 自己退出；磁盘上 meta 行数 == 向量行数，且比开始时多。
// 用法：node scripts/test-hard-kill.mjs <nodePort> <pyPort>   （需要先以 PORT/SEARCH_PORT/JSS_INDEX_DIR 起好一套 Node+Python）
import { execSync } from "node:child_process";
import fs from "node:fs";

const nodePort = process.argv[2] || "8791";
const pyPort = process.argv[3] || "8790";
const indexDir = process.env.JSS_INDEX_DIR;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function j(base, path, body) {
  const res = await fetch(`${base}${path}`, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return res.json();
}
function listeningPid(port) {
  try {
    const out = execSync(`netstat -ano | findstr LISTENING | findstr :${port}`, { encoding: "utf8" });
    const m = out.split(/\r?\n/).find((l) => l.includes(`:${port} `));
    return m ? Number(m.trim().split(/\s+/).pop()) : null;
  } catch {
    return null;
  }
}
function pidAlive(pid) {
  try {
    return execSync(`tasklist /FI "PID eq ${pid}" /NH`, { encoding: "utf8" }).includes(String(pid));
  } catch {
    return false;
  }
}
function diskCount() {
  const sig = fs.readdirSync(indexDir).find((d) => fs.existsSync(`${indexDir}/${d}/meta.json`));
  const meta = JSON.parse(fs.readFileSync(`${indexDir}/${sig}/meta.json`, "utf8"));
  // .npy 头部 6 字节 magic + 版本 + 头长；shape 写在头里，直接解析
  const buf = fs.readFileSync(`${indexDir}/${sig}/vectors_color_full.npy`);
  const headerLen = buf.readUInt16LE(8);
  const header = buf.toString("latin1", 10, 10 + headerLen);
  const rows = Number(/'shape':\s*\((\d+)/.exec(header)?.[1]);
  return { meta: meta.items.length, npyRows: rows, tmp: fs.readdirSync(`${indexDir}/${sig}`).filter((f) => f.endsWith(".tmp")) };
}

const node = `http://localhost:${nodePort}`;
let st;
for (let i = 0; i < 60; i++) {
  try { st = await j(node, "/api/status"); if (st.services?.python === "ok") break; } catch { /* not up */ }
  await sleep(2000);
}
if (st?.services?.python !== "ok") { console.log("FAIL: stack not ready", st?.services); process.exit(1); }
const before = diskCount();
console.log(`before: disk meta=${before.meta} npy=${before.npyRows} | kernel indexed=${st.index.indexed}`);

await j(node, "/api/index/start", {});
await sleep(15000);
st = await j(node, "/api/status");
console.log(`15s in: running=${st.index.indexing.running} done=${st.index.indexing.done} rate=${st.index.indexing.rate_per_sec}/s | disk meta=${diskCount().meta}`);

const nodePid = listeningPid(nodePort);
const pyPid = listeningPid(pyPort);
console.log(`hard-killing node pid ${nodePid} (python pid ${pyPid} left alone, like closing the window)`);
execSync(`taskkill /PID ${nodePid} /F`, { stdio: "ignore" });

const t0 = Date.now();
let alive = true;
while (Date.now() - t0 < 120000) {
  await sleep(1000);
  alive = pidAlive(pyPid);
  if (!alive) break;
}
const after = diskCount();
console.log(`python exited on its own: ${!alive} after ${Math.round((Date.now() - t0) / 1000)}s`);
console.log(`after: disk meta=${after.meta} npy=${after.npyRows} tmp=${JSON.stringify(after.tmp)}`);
const ok = !alive && after.meta === after.npyRows && after.meta > before.meta && after.tmp.length === 0;
console.log(ok ? "HARD-KILL TEST PASS" : "HARD-KILL TEST FAIL");
process.exit(ok ? 0 : 1);

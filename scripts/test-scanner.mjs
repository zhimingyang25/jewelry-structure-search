// 扫描逻辑干跑测试：不写盘，只验证 rescanCatalog / summarizeRoots / removeRoot 的行为。
import fs from "node:fs";
import path from "node:path";
import { removeRoot, rescanCatalog, summarizeRoots } from "../src/scanner.mjs";

const cat = JSON.parse(fs.readFileSync("data/images.json", "utf8"));
const ext = [".jpg", ".jpeg", ".png", ".bmp"];

let t = Date.now();
const r = rescanCatalog(cat, { extensions: ext });
console.log(`rescan ${cat.imageRoots.length} roots in ${Date.now() - t} ms ->`, JSON.stringify({ count: r.catalog.count, added: r.added, removed: r.removed, changed: r.changed, skipped: r.skippedRoots.length }));
console.log("id stability (first 3):", [0, 1, 2].every((i) => r.catalog.images[i].id === cat.images[i].id && r.catalog.images[i].path === cat.images[i].path));
for (const s of summarizeRoots(r.catalog)) console.log("  ", String(s.count).padStart(6), s.exists ? "ok     " : "MISSING", s.path);

// 根目录暂时不在（移动硬盘没插）：记录应保留，不算 removed
const fakeRoot = "Z:\\not-plugged";
const fake = { ...cat, imageRoots: [...cat.imageRoots, fakeRoot], images: [...cat.images, { path: path.join(fakeRoot, "a.jpg"), file_name: "a.jpg", size: 1, mtime_ms: 1 }] };
const r2 = rescanCatalog(fake, { extensions: ext });
console.log("unplugged root kept:", r2.skippedRoots.length === 1 && r2.catalog.images.some((i) => i.path.startsWith("Z:")), "| removed =", r2.removed);

// 删掉磁盘上不存在的记录
const ghost = { ...cat, images: [...cat.images, { path: path.join(cat.imageRoots[0], "ghost-not-on-disk.jpg"), file_name: "ghost.jpg", size: 1, mtime_ms: 1 }] };
const r3 = rescanCatalog(ghost, { extensions: ext });
console.log("ghost file removed:", r3.removed === 1 && !r3.catalog.images.some((i) => i.file_name === "ghost.jpg"));

// 新增一个不存在的目录应报错
try {
  rescanCatalog(cat, { addRoot: "Q:\\definitely-missing", extensions: ext });
  console.log("addRoot missing dir: NO ERROR (bad)");
} catch (e) {
  console.log("addRoot missing dir -> error OK:", e.message.slice(0, 40));
}

// 移除一个根目录
const first = cat.imageRoots[0];
const r4 = removeRoot(r.catalog, first);
console.log(`removeRoot ${path.basename(first)} -> removed ${r4.removed}, roots left ${r4.catalog.imageRoots.length}, count ${r4.catalog.count}`);

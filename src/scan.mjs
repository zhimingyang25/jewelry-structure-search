import { dataPath, loadConfig, readJson, writeJson } from "./common.mjs";
import { rescanCatalog } from "./scanner.mjs";

// npm run scan：重新扫描全部已登记的根目录（config.imageRoot 若还没登记则顺带加入）。
// 不再用一次单目录扫描覆盖整个清单，否则会把其它已登记的文件夹全部丢掉。
const config = loadConfig();
const existing = readJson(dataPath(config, "images.json"), { images: [] });
const { catalog, added, removed, changed, skippedRoots } = rescanCatalog(existing, {
  addRoot: config.imageRoot || null,
  extensions: config.imageExtensions
});
writeJson(dataPath(config, "images.json"), catalog);

console.log(`共 ${catalog.count} 张，${catalog.imageRoots.length} 个根目录；新增 ${added}，移除 ${removed}，变动 ${changed}`);
for (const root of skippedRoots) console.log(`跳过（找不到）：${root}`);

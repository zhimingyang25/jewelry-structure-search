import { dataPath, loadConfig, writeJson } from "./common.mjs";
import { scanImages } from "./scanner.mjs";

const config = loadConfig();
const catalog = scanImages(config);
writeJson(dataPath(config, "images.json"), catalog);

console.log(`Scanned ${catalog.count} images from ${catalog.imageRoot}`);

import { dataPath, loadConfig, loadEnv, parseArgs, readJson, writeJson } from "./common.mjs";
import { analyzeJewelryImage } from "./openai-client.mjs";
import { getImageTags, migrateLegacyIdTags, tagKey } from "./tag-store.mjs";

loadEnv();
const config = loadConfig();
const args = parseArgs(globalThis.process?.argv || ["node", "src/tag-openai.mjs"]);
const catalog = readJson(dataPath(config, "images.json"), { images: [] });
const tagDb = migrateLegacyIdTags(config, catalog);
const failedDb = readJson(dataPath(config, "tag-failures.json"), { failures: [] });
const limit = Number(args.limit || 20);
const offset = Number(args.offset || 0);
const onlyUntagged = args["only-untagged"] !== false;
const retries = Number(args.retries || 2);
let processed = 0;
let attempted = 0;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shouldRetry(error) {
  const message = String(error?.message || "").toLowerCase();
  if (message.includes("does not support image inputs")) return false;
  if (message.includes("413") || message.includes("request entity too large")) return false;
  if (message.includes("asset upload returned 403") || message.includes("upstream_error")) return false;
  if (message.includes("image file is too large")) return false;
  if (message.includes("image compression failed")) return false;
  if (message.includes("automatic compression failed")) return false;
  if (message.includes("missing openai_api_key")) return false;
  return true;
}

function isFatalConfigError(error) {
  const message = String(error?.message || "").toLowerCase();
  return message.includes("does not support image inputs") || message.includes("missing openai_api_key");
}

for (const image of catalog.images.slice(offset)) {
  if (attempted >= limit) break;
  if (onlyUntagged && getImageTags(tagDb, image)) continue;
  attempted += 1;
  let lastError = null;
  for (let attempt = 1; attempt <= retries + 1; attempt += 1) {
    try {
      const tags = await analyzeJewelryImage({ filePath: image.path });
      tagDb.tags[tagKey(image)] = {
        ...tags,
        image_id: image.id,
        file_name: image.file_name,
        tagged_at: new Date().toISOString(),
        provider: "openai"
      };
      processed += 1;
      console.log(`[${attempted}/${limit}] ${image.relative_path} -> ${tags.category}, ${tags.stone_shape}`);
      writeJson(dataPath(config, "structure-tags.json"), tagDb);
      lastError = null;
      break;
    } catch (error) {
      lastError = error;
      if (attempt <= retries && shouldRetry(error)) {
        console.warn(`Retry ${attempt}/${retries}: ${image.relative_path}`);
        await sleep(1200 * attempt);
      }
    }
  }
  if (lastError) {
    console.error(`Failed: ${image.relative_path}`);
    console.error(lastError.message);
    failedDb.failures.push({
      image_id: image.id,
      file_name: image.file_name,
      relative_path: image.relative_path,
      failed_at: new Date().toISOString(),
      error: lastError.message
    });
    writeJson(dataPath(config, "tag-failures.json"), failedDb);
    if (isFatalConfigError(lastError)) {
      console.error("Fatal configuration error. Please switch OPENAI_MODEL to a vision-capable model before continuing.");
      break;
    }
  }
}

console.log(`Tagged ${processed} images. Attempted ${attempted} images.`);

import { dataPath, loadConfig, readJson, writeJson } from "./common.mjs";
import { migrateLegacyIdTags, tagKey } from "./tag-store.mjs";

const config = loadConfig();
const catalog = readJson(dataPath(config, "images.json"), { images: [] });
const tagDb = migrateLegacyIdTags(config, catalog);
const pathKeys = new Set((catalog.images || []).map((image) => tagKey(image)));
const pathTaggedFileNames = new Set();
for (const pathKey of pathKeys) {
  const tag = tagDb.tags?.[pathKey];
  if (tag?.file_name) pathTaggedFileNames.add(tag.file_name);
}
let removed = 0;

for (const key of Object.keys(tagDb.tags || {})) {
  if (!/^\d+$/.test(key)) continue;
  const legacyTag = tagDb.tags[key];
  const hasPathCopy = legacyTag?.file_name && pathTaggedFileNames.has(legacyTag.file_name);
  if (hasPathCopy) {
    delete tagDb.tags[key];
    removed += 1;
  }
}

writeJson(dataPath(config, "structure-tags.json"), tagDb);
console.log(`Removed ${removed} legacy numeric tag keys.`);

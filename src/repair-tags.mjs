import { dataPath, loadConfig, readJson, writeJson } from "./common.mjs";
import { migrateLegacyIdTags } from "./tag-store.mjs";

const config = loadConfig();
const catalog = readJson(dataPath(config, "images.json"), { images: [] });
const tagDb = migrateLegacyIdTags(config, catalog);
let removed = 0;

for (const image of catalog.images || []) {
  const tag = tagDb.tags?.[image.path];
  if (!tag) continue;
  if (tag.file_name && tag.file_name !== image.file_name) {
    delete tagDb.tags[image.path];
    removed += 1;
  }
}

writeJson(dataPath(config, "structure-tags.json"), tagDb);
console.log(`Removed ${removed} mismatched path tags.`);

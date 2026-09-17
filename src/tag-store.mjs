import { dataPath, readJson, writeJson } from "./common.mjs";

export function tagKey(image) {
  return image.path;
}

export function loadTagDb(config) {
  return readJson(dataPath(config, "structure-tags.json"), { tags: {} });
}

export function getImageTags(tagDb, image) {
  return tagDb.tags?.[tagKey(image)] || null;
}

export function countTaggedImages(catalog, tagDb) {
  return (catalog.images || []).filter((image) => getImageTags(tagDb, image)).length;
}

export function migrateLegacyIdTags(config, catalog) {
  const tagDb = loadTagDb(config);
  let changed = false;
  const legacyTags = Object.entries(tagDb.tags || {})
    .filter(([key]) => !String(key).includes(":\\") && !String(key).startsWith("\\\\"))
    .map(([, tag]) => tag)
    .filter(Boolean);

  for (const image of catalog.images || []) {
    const legacy = tagDb.tags?.[String(image.id)];
    const key = tagKey(image);
    if (legacy && !tagDb.tags[key] && legacy.file_name === image.file_name) {
      tagDb.tags[key] = legacy;
      changed = true;
      continue;
    }

    if (!tagDb.tags[key]) {
      const sameFileLegacy = legacyTags.find((tag) => tag.file_name === image.file_name);
      if (sameFileLegacy) {
        tagDb.tags[key] = sameFileLegacy;
        changed = true;
      }
    }
  }
  if (changed) {
    writeJson(dataPath(config, "structure-tags.json"), tagDb);
  }
  return tagDb;
}

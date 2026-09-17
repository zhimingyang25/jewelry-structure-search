import fs from "node:fs";
import path from "node:path";

export function scanImages(config) {
  const root = path.resolve(config.imageRoot);
  const extensions = new Set((config.imageExtensions || [".jpg", ".jpeg", ".png"]).map((ext) => ext.toLowerCase()));
  const images = [];

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(fullPath);
        continue;
      }
      const ext = path.extname(entry.name).toLowerCase();
      if (!extensions.has(ext)) continue;
      const stat = fs.statSync(fullPath);
      const relativePath = path.relative(root, fullPath);
      images.push({
        id: images.length + 1,
        path: fullPath,
        file_name: entry.name,
        base_name: path.basename(entry.name, ext),
        folder: path.dirname(relativePath),
        relative_path: relativePath,
        ext,
        size: stat.size,
        mtime_ms: Math.round(stat.mtimeMs)
      });
    }
  }

  if (!fs.existsSync(root)) {
    throw new Error(`Image root does not exist: ${root}`);
  }

  walk(root);
  return {
    imageRoot: root,
    scannedAt: new Date().toISOString(),
    count: images.length,
    images
  };
}

export function mergeCatalogs(existingCatalog, scannedCatalog) {
  const byPath = new Map();
  for (const image of existingCatalog.images || []) {
    byPath.set(image.path, image);
  }
  for (const image of scannedCatalog.images || []) {
    byPath.set(image.path, image);
  }
  const images = [...byPath.values()].map((image, index) => ({
    ...image,
    id: index + 1
  }));
  const roots = [
    ...(existingCatalog.imageRoots || (existingCatalog.imageRoot ? [existingCatalog.imageRoot] : [])),
    scannedCatalog.imageRoot
  ].filter(Boolean);
  return {
    imageRoot: roots[0] || scannedCatalog.imageRoot,
    imageRoots: [...new Set(roots)],
    scannedAt: new Date().toISOString(),
    count: images.length,
    images
  };
}

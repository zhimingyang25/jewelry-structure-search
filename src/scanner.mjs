import fs from "node:fs";
import path from "node:path";

const DEFAULT_EXTENSIONS = [".jpg", ".jpeg", ".png", ".bmp"];

function normalizeRoot(root) {
  return path.resolve(String(root || "").trim());
}

function isUnder(filePath, root) {
  return filePath === root || filePath.startsWith(root.endsWith(path.sep) ? root : root + path.sep);
}

function extensionSet(extensions) {
  return new Set((extensions || DEFAULT_EXTENSIONS).map((ext) => ext.toLowerCase()));
}

/** 递归扫描一个根目录，返回该目录下所有图片的记录（不含 id）。根目录不存在则抛错。 */
export function scanRoot(root, extensions) {
  const resolvedRoot = normalizeRoot(root);
  const allowed = extensionSet(extensions);
  const images = [];

  function walk(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return; // 单个子目录没权限或被占用：跳过，不让整次扫描失败
    }
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(fullPath);
        continue;
      }
      const ext = path.extname(entry.name).toLowerCase();
      if (!allowed.has(ext)) continue;
      let stat;
      try {
        stat = fs.statSync(fullPath);
      } catch {
        continue;
      }
      const relativePath = path.relative(resolvedRoot, fullPath);
      images.push({
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

  if (!fs.existsSync(resolvedRoot) || !fs.statSync(resolvedRoot).isDirectory()) {
    throw new Error(`Image root does not exist: ${resolvedRoot}`);
  }
  walk(resolvedRoot);
  return { imageRoot: resolvedRoot, count: images.length, images };
}

/** 兼容旧调用：按 config.imageRoot 扫一个目录并编 id。 */
export function scanImages(config) {
  const result = scanRoot(config.imageRoot, config.imageExtensions);
  return {
    imageRoot: result.imageRoot,
    scannedAt: new Date().toISOString(),
    count: result.count,
    images: result.images.map((image, index) => ({ id: index + 1, ...image }))
  };
}

export function catalogRoots(catalog) {
  const roots = catalog?.imageRoots || (catalog?.imageRoot ? [catalog.imageRoot] : []);
  return [...new Set(roots.map(normalizeRoot))];
}

function finalizeCatalog(images, roots) {
  return {
    imageRoot: roots[0] || "",
    imageRoots: roots,
    scannedAt: new Date().toISOString(),
    count: images.length,
    images: images.map((image, index) => ({ ...image, id: index + 1 }))
  };
}

/**
 * 重新扫描全部已登记的根目录（可顺带新增一个），把清单同步成磁盘上的真实状态：
 * - 新文件：加入；
 * - 已登记目录下消失的文件：移出清单（认图时会把它们的向量一并清掉）；
 * - 大小或修改时间变了的文件：更新记录（认图时会重算）；
 * - 根目录本身找不到（移动硬盘没插）：跳过，该目录下的记录原样保留，不误删。
 * 已有记录保持原来的顺序，所以老图的 id 不变。
 */
export function rescanCatalog(existingCatalog, { addRoot = null, extensions } = {}) {
  const roots = catalogRoots(existingCatalog);
  if (addRoot) {
    const added = normalizeRoot(addRoot);
    if (!fs.existsSync(added) || !fs.statSync(added).isDirectory()) {
      throw new Error(`扫描目录不存在或不是文件夹：${added}`);
    }
    if (!roots.includes(added)) roots.push(added);
  }

  const scanned = new Map();
  const skippedRoots = [];
  const rootCounts = {};
  for (const root of roots) {
    let result;
    try {
      result = scanRoot(root, extensions);
    } catch {
      skippedRoots.push(root);
      continue;
    }
    rootCounts[root] = result.count;
    for (const image of result.images) scanned.set(image.path, image);
  }

  const images = [];
  let removed = 0;
  let changed = 0;
  const seen = new Set();
  for (const image of existingCatalog?.images || []) {
    const fresh = scanned.get(image.path);
    if (fresh) {
      if (fresh.size !== image.size || fresh.mtime_ms !== image.mtime_ms) changed += 1;
      images.push({ ...image, ...fresh });
      seen.add(image.path);
      continue;
    }
    if (skippedRoots.some((root) => isUnder(image.path, root))) {
      images.push(image); // 根目录暂时不在，保留
      seen.add(image.path);
      rootCounts[skippedRoots.find((root) => isUnder(image.path, root))] = (rootCounts[skippedRoots.find((root) => isUnder(image.path, root))] || 0) + 1;
      continue;
    }
    removed += 1;
  }
  let added = 0;
  for (const image of scanned.values()) {
    if (seen.has(image.path)) continue;
    images.push(image);
    added += 1;
  }
  return { catalog: finalizeCatalog(images, roots), added, removed, changed, skippedRoots, rootCounts };
}

/** 移除一个已登记的根目录，并把只属于它的记录移出清单。不碰磁盘。 */
export function removeRoot(existingCatalog, root) {
  const target = normalizeRoot(root);
  const roots = catalogRoots(existingCatalog).filter((r) => r !== target);
  const images = (existingCatalog?.images || []).filter((image) => !isUnder(image.path, target) || roots.some((r) => isUnder(image.path, r)));
  const removed = (existingCatalog?.images || []).length - images.length;
  return { catalog: finalizeCatalog(images, roots), removed };
}

/** 各根目录当前在清单里的张数，以及根目录是否还在磁盘上。 */
export function summarizeRoots(catalog) {
  const roots = catalogRoots(catalog);
  const counts = Object.fromEntries(roots.map((root) => [root, 0]));
  for (const image of catalog?.images || []) {
    const root = roots.find((r) => isUnder(image.path, r));
    if (root) counts[root] += 1;
  }
  return roots.map((root) => ({ path: root, count: counts[root], exists: fs.existsSync(root) }));
}

/** 兼容旧调用：把一次单目录扫描合并进现有清单。 */
export function mergeCatalogs(existingCatalog, scannedCatalog) {
  const byPath = new Map();
  for (const image of existingCatalog.images || []) byPath.set(image.path, image);
  for (const image of scannedCatalog.images || []) byPath.set(image.path, image);
  const roots = [...new Set([...catalogRoots(existingCatalog), normalizeRoot(scannedCatalog.imageRoot)].filter(Boolean))];
  return finalizeCatalog([...byPath.values()], roots);
}

import fs from "node:fs";
import path from "node:path";

export function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

export function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf8");
}

export function loadConfig() {
  const configPath = path.resolve("config.json");
  return readJson(configPath, {});
}

export function loadEnv(filePath = ".env") {
  const resolved = path.resolve(filePath);
  const localEnv = {};
  if (!fs.existsSync(resolved)) {
    globalThis.__LOCAL_ENV__ = globalThis.__LOCAL_ENV__ || {};
    return globalThis.__LOCAL_ENV__;
  }
  const lines = fs.readFileSync(resolved, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const equalsAt = trimmed.indexOf("=");
    if (equalsAt === -1) continue;
    const key = trimmed.slice(0, equalsAt).trim();
    let value = trimmed.slice(equalsAt + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    localEnv[key] = value;
    if (globalThis.process?.env && !globalThis.process.env[key]) {
      globalThis.process.env[key] = value;
    }
  }
  globalThis.__LOCAL_ENV__ = { ...(globalThis.__LOCAL_ENV__ || {}), ...localEnv };
  return globalThis.__LOCAL_ENV__;
}

export function dataPath(config, name) {
  return path.resolve(config.dataDir || "data", name);
}

export function mimeFromPath(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".webp") return "image/webp";
  if (ext === ".bmp") return "image/bmp";
  return "image/jpeg";
}

export function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const part = argv[i];
    if (!part.startsWith("--")) continue;
    const key = part.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

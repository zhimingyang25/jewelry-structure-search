import fs from "node:fs";
import childProcess from "node:child_process";
import crypto from "node:crypto";
import path from "node:path";
import { promisify } from "node:util";
import { mimeFromPath } from "./common.mjs";
import { normalizeTags, taxonomy } from "./taxonomy.mjs";

const execFile = promisify(childProcess.execFile);

function imageConfig() {
  const env = { ...(globalThis.__LOCAL_ENV__ || {}), ...(globalThis.process?.env || {}) };
  const maxImageBytes = Number(env.MAX_IMAGE_BYTES || 4 * 1024 * 1024);
  const maxImageSide = Number(env.MAX_IMAGE_SIDE || 1600);
  const jpegQuality = Number(env.IMAGE_JPEG_QUALITY || 82);
  return {
    maxImageBytes,
    uploadProfiles: [
      { side: maxImageSide, quality: jpegQuality },
      { side: 1200, quality: 78 },
      { side: 900, quality: 72 },
      { side: 700, quality: 68 },
      { side: 512, quality: 60 },
      { side: 384, quality: 55 },
      { side: 256, quality: 50 }
    ]
  };
}

function describeProfile(profile) {
  return `${profile.side}px/${profile.quality}%`;
}

async function tryCompressImage(filePath, profile) {
  try {
    const sharp = (await import("sharp")).default;
    const bytes = await sharp(filePath, { failOn: "none" })
      .rotate()
      .resize({
        width: profile.side,
        height: profile.side,
        fit: "inside",
        withoutEnlargement: true
      })
      .jpeg({ quality: profile.quality, mozjpeg: true })
      .toBuffer();
    return `data:image/jpeg;base64,${bytes.toString("base64")}`;
  } catch {
    return null;
  }
}

function convertedImagePath(filePath, profile) {
  const stat = fs.statSync(filePath);
  const hash = crypto
    .createHash("sha1")
    .update(`${filePath}|${stat.size}|${stat.mtimeMs}|${profile.side}|${profile.quality}`)
    .digest("hex");
  return path.resolve("data", "converted-images", `${hash}.jpg`);
}

async function tryWindowsConvertImage(filePath, profile) {
  if (globalThis.process?.platform !== "win32") return null;
  const outputPath = convertedImagePath(filePath, profile);
  try {
    if (!fs.existsSync(outputPath)) {
      const scriptPath = path.resolve("src", "convert-image.ps1");
      await execFile("powershell.exe", [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        scriptPath,
        "-InputPath",
        filePath,
        "-OutputPath",
        outputPath,
        "-MaxSide",
        String(profile.side),
        "-Quality",
        String(profile.quality)
      ], { windowsHide: true, timeout: 120000 });
    }
    const bytes = fs.readFileSync(outputPath);
    return `data:image/jpeg;base64,${bytes.toString("base64")}`;
  } catch {
    return null;
  }
}

async function compressImage(filePath, profile) {
  return await tryCompressImage(filePath, profile) || await tryWindowsConvertImage(filePath, profile);
}

async function imageDataUrl(filePath, profile = null) {
  const { maxImageBytes, uploadProfiles } = imageConfig();
  if (profile) {
    const compressed = await compressImage(filePath, profile);
    if (compressed) {
      const payloadBytes = Math.ceil((compressed.length - "data:image/jpeg;base64,".length) * 0.75);
      if (payloadBytes <= maxImageBytes) return compressed;
    }
    throw new Error(`Image compression failed or still too large at ${describeProfile(profile)}.`);
  }
  const compressed = await compressImage(filePath, uploadProfiles[0]);
  if (compressed) return compressed;

  const stat = fs.statSync(filePath);
  if (stat.size > maxImageBytes) {
    throw new Error(`Image file is too large for API upload (${Math.round(stat.size / 1024 / 1024)} MB), and automatic compression failed.`);
  }
  const bytes = fs.readFileSync(filePath);
  return `data:${mimeFromPath(filePath)};base64,${bytes.toString("base64")}`;
}

function extractJson(text) {
  const trimmed = String(text || "").trim();
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const raw = fenced ? fenced[1].trim() : trimmed;
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end === -1) throw new Error(`No JSON object in model output: ${trimmed.slice(0, 200)}`);
  return JSON.parse(raw.slice(start, end + 1));
}

function buildPrompt(promptExtra = "") {
  return [
    "You are building a retrieval index for a jewelry CAD model library.",
    "Analyze the image as jewelry structure, not as photography style.",
    "Ignore hands, props, background, watermark, lighting, and metal color unless structure depends on it.",
    "Return only valid JSON with these exact keys:",
    "category, stone_shape, settings, band_types, structures, styles, material_color, reuse_keywords, description, confidence.",
    `Allowed taxonomy: ${JSON.stringify(taxonomy)}`,
    "Use English snake_case values where possible. confidence is 0 to 1.",
    promptExtra
  ].filter(Boolean).join("\n");
}

function extractTextFromChatContent(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === "string") return item;
        if (item?.type === "text") return item.text || "";
        return "";
      })
      .join("\n");
  }
  return "";
}

async function parseApiResponse(response, { normalize = true } = {}) {
  const raw = await response.text();
  if (!response.ok) {
    throw new Error(`OpenAI API error ${response.status}: ${raw.slice(0, 500)}`);
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    const sseText = raw
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.replace(/^data:\s*/, ""))
      .filter((line) => line && line !== "[DONE]")
      .join("");
    payload = JSON.parse(sseText);
  }

  const text =
    payload.output_text ||
    payload.output?.flatMap((item) => item.content || [])
      ?.filter((content) => content.type === "output_text")
      ?.map((content) => content.text)
      ?.join("\n") ||
    extractTextFromChatContent(payload.choices?.[0]?.message?.content) ||
    "";

  const json = extractJson(text);
  return normalize ? normalizeTags(json) : json;
}

function isTooLargeError(error) {
  const message = String(error?.message || "");
  return message.includes("413") || message.toLowerCase().includes("request entity too large");
}

function isAssetUploadError(error) {
  const message = String(error?.message || "").toLowerCase();
  return message.includes("asset upload returned 403") || message.includes("upstream_error");
}

async function requestTags({ apiMode, baseUrl, apiKey, model, prompt, imageUrl, signal, normalize = true }) {
  if (apiMode === "responses") {
    const response = await fetch(`${baseUrl}/responses`, {
      method: "POST",
      signal,
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model,
        input: [
          {
            role: "user",
            content: [
              { type: "input_text", text: prompt },
              { type: "input_image", image_url: imageUrl, detail: "low" }
            ]
          }
        ]
      })
    });
    return parseApiResponse(response, { normalize });
  }

  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    signal,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model,
      stream: false,
      response_format: { type: "json_object" },
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: prompt },
            { type: "image_url", image_url: { url: imageUrl } }
          ]
        }
      ]
    })
  });
  return parseApiResponse(response, { normalize });
}

function apiSettings() {
  const env = { ...(globalThis.__LOCAL_ENV__ || {}), ...(globalThis.process?.env || {}) };
  const apiKey = env.OPENAI_API_KEY;
  if (!apiKey) {
    throw new Error("Missing OPENAI_API_KEY environment variable.");
  }
  return {
    apiKey,
    model: env.OPENAI_MODEL || "gpt-4.1-mini",
    baseUrl: (env.OPENAI_BASE_URL || "https://api.openai.com/v1").replace(/\/+$/, ""),
    apiMode: (env.OPENAI_API_MODE || "chat").toLowerCase()
  };
}

/** 通用：给一段 prompt 和一张 data URL，返回模型输出里的 JSON 对象（不做标签归一化）。 */
export async function chatWithImage({ prompt, imageUrl, signal }) {
  const { apiKey, model, baseUrl, apiMode } = apiSettings();
  return requestTags({ apiMode, baseUrl, apiKey, model, prompt, imageUrl, signal, normalize: false });
}

export async function analyzeJewelryImage({ filePath, imageDataUrl: providedDataUrl, promptExtra = "" }) {
  const { apiKey, model, baseUrl, apiMode } = apiSettings();
  const prompt = buildPrompt(promptExtra);

  if (providedDataUrl) {
    return requestTags({ apiMode, baseUrl, apiKey, model, prompt, imageUrl: providedDataUrl });
  }

  let lastError = null;
  const { uploadProfiles } = imageConfig();
  for (const profile of uploadProfiles) {
    try {
      const imageUrl = await imageDataUrl(filePath, profile);
      return await requestTags({ apiMode, baseUrl, apiKey, model, prompt, imageUrl });
    } catch (error) {
      lastError = error;
      if (!isTooLargeError(error) && !isAssetUploadError(error)) throw error;
    }
  }
  if (lastError && (isTooLargeError(lastError) || isAssetUploadError(lastError))) {
    throw new Error(`${lastError.message} | tried_profiles=${uploadProfiles.map(describeProfile).join(", ")}`);
  }
  throw lastError;
}

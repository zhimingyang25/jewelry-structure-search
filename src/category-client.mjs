import { chatWithImage } from "./openai-client.mjs";

// 查询图品类 + 主体框（design.md §3.3/§3.4）。只问两件事，不再让 GPT 填结构标签。
const CATEGORIES = ["ring", "pendant", "necklace", "earring", "bracelet", "bangle", "brooch", "charm", "other", "unclear"];

const PROMPT = [
  "You are looking at a photo, render, or sketch that contains jewelry.",
  "Task 1: decide the jewelry category of the LARGEST, MOST PROMINENT single piece in the image.",
  `Choose exactly one of: ${CATEGORIES.join(", ")}. Use "unclear" only if you truly cannot tell.`,
  "Task 2: give the bounding box of that same piece as normalized coordinates [x0, y0, x1, y1] in 0..1 (top-left origin).",
  "Ignore hands, necks, boxes, props, background, and other smaller pieces.",
  'Return only JSON: {"category": "...", "main_item_box": [x0, y0, x1, y1], "confidence": 0..1}'
].join("\n");

function clamp01(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.min(1, Math.max(0, n));
}

function normalizeBox(box) {
  if (!Array.isArray(box) || box.length !== 4) return null;
  const b = box.map(clamp01);
  if (b.some((v) => v === null)) return null;
  const [x0, y0, x1, y1] = b;
  if (x1 <= x0 || y1 <= y0) return null;
  const area = (x1 - x0) * (y1 - y0);
  if (area < 0.05 || area > 0.95) return null;
  return b;
}

export async function detectCategoryAndBox({ imageDataUrl, timeoutMs = 12000 }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const json = await chatWithImage({ prompt: PROMPT, imageUrl: imageDataUrl, signal: controller.signal });
    const category = String(json.category || "unclear").toLowerCase().trim();
    return {
      category: CATEGORIES.includes(category) ? category : "unclear",
      box: normalizeBox(json.main_item_box),
      confidence: Number(json.confidence) || 0,
      source: "gpt"
    };
  } finally {
    clearTimeout(timer);
  }
}

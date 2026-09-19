import { chatWithImage } from "./openai-client.mjs";

// 查询图品类 + 主体框（design.md §3.3/§3.4）。只问两件事，不再让 GPT 填结构标签。
const CATEGORIES = ["ring", "pendant", "necklace", "earring", "bracelet", "bangle", "brooch", "charm", "other", "unclear"];

const PROMPT = [
  "You are looking at a photo, render, or sketch that contains jewelry.",
  "Task 1: decide the jewelry category of the LARGEST, MOST PROMINENT single piece in the image.",
  `Choose exactly one of: ${CATEGORIES.join(", ")}. Use "unclear" only if you truly cannot tell.`,
  "Task 2: give the bounding box of that same piece as [x0, y0, x1, y1], each a fraction of image width/height between 0 and 1, top-left origin, with x0 < x1 and y0 < y1.",
  "Ignore hands, necks, boxes, props, background, and other smaller pieces.",
  'Return only JSON, for example: {"category": "ring", "main_item_box": [0.12, 0.08, 0.88, 0.93], "confidence": 0.9}'
].join("\n");

function normalizeBox(box) {
  if (!Array.isArray(box) || box.length !== 4) return null;
  let b = box.map(Number);
  if (b.some((v) => !Number.isFinite(v) || v < 0)) return null;
  // 有些模型（尤其 Gemini 系）无视要求，按 0～1000 的整数比例给框；全部 ≤1000 且有值 >1 时按该比例换算。
  // 真正的像素坐标无法和它区分，但这里图片最长边 ≤1600，换算后超出 [0,1] 的会被下面的面积检查挡掉。
  if (b.some((v) => v > 1)) {
    if (b.every((v) => v <= 1000)) b = b.map((v) => v / 1000);
    else return null;
  }
  const [x0, y0, x1, y1] = b.map((v) => Math.min(1, v));
  if (x1 <= x0 || y1 <= y0) return null;
  const area = (x1 - x0) * (y1 - y0);
  if (area < 0.05 || area > 0.95) return null;
  return [x0, y0, x1, y1];
}

export async function detectCategoryAndBox({ imageDataUrl, timeoutMs = 30000 }) {
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

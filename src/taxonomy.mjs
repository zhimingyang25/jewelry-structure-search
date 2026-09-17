export const taxonomy = {
  categories: [
    "ring",
    "pendant",
    "earring",
    "bracelet",
    "bangle",
    "necklace",
    "brooch",
    "charm",
    "other",
    "unclear"
  ],
  stoneShapes: [
    "round",
    "oval",
    "pear",
    "marquise",
    "emerald",
    "princess",
    "cushion",
    "heart",
    "baguette",
    "trillion",
    "none",
    "unclear"
  ],
  settings: [
    "prong",
    "bezel",
    "pave",
    "micro_pave",
    "channel",
    "halo",
    "cluster",
    "tension",
    "flush",
    "invisible",
    "unclear"
  ],
  bandTypes: [
    "straight",
    "split_shank",
    "twisted",
    "bypass",
    "open",
    "wide",
    "thin",
    "pave_band",
    "plain_band",
    "filigree",
    "unclear"
  ],
  structures: [
    "single_center",
    "three_stone",
    "halo_center",
    "cluster_top",
    "flower",
    "animal",
    "geometric",
    "heart_motif",
    "bow",
    "crown",
    "cross",
    "chain",
    "drop",
    "layered",
    "hollow",
    "engraved",
    "asymmetric",
    "unclear"
  ],
  styles: [
    "minimal",
    "luxury",
    "bridal",
    "vintage",
    "classic",
    "fashion",
    "cartoon",
    "chinese_style",
    "floral",
    "geometric",
    "unclear"
  ],
  materialColors: [
    "white",
    "yellow_gold",
    "rose_gold",
    "two_tone",
    "black",
    "unclear"
  ]
};

const categoryAliases = {
  rings: "ring",
  earrings: "earring",
  studs: "earring",
  hoop: "earring",
  hoops: "earring",
  hoops_earring: "earring",
  drop_earring: "earring",
  pendant_necklace: "necklace",
  necklace_pendant: "necklace",
  unclear: "unclear",
  none: "unclear",
  unknown: "unclear"
};

const stoneShapeAliases = {
  circular: "round",
  brilliant: "round",
  brilliant_cut: "round",
  round_brilliant: "round",
  teardrop: "pear",
  tear_drop: "pear",
  emerald_cut: "emerald",
  radiant: "emerald",
  square: "princess",
  square_cut: "princess",
  cushion_cut: "cushion",
  marquis: "marquise",
  navette: "marquise",
  triangle: "trillion",
  triangular: "trillion",
  trillion_cut: "trillion",
  no_stone: "none",
  none: "none",
  unclear: "unclear",
  unknown: "unclear"
};

const settingAliases = {
  claws: "prong",
  claw: "prong",
  prongs: "prong",
  bezel_set: "bezel",
  pave_set: "pave",
  micro_pave_set: "micro_pave",
  channel_set: "channel",
  halo_set: "halo",
  cluster_setting: "cluster",
  tension_set: "tension",
  gypsy: "flush",
  invisible_set: "invisible"
};

const bandTypeAliases = {
  split: "split_shank",
  split_band: "split_shank",
  twist: "twisted",
  twisted_band: "twisted",
  bypass_band: "bypass",
  open_band: "open",
  wide_band: "wide",
  thin_band: "thin",
  plain: "plain_band"
};

const structureAliases = {
  solitaire: "single_center",
  single_stone: "single_center",
  center_stone: "single_center",
  three_stones: "three_stone",
  trilogy: "three_stone",
  halo_ring: "halo_center",
  floral: "flower",
  blossom: "flower",
  geometric_pattern: "geometric",
  heart: "heart_motif",
  chain_link: "chain",
  dangling: "drop",
  dangling_drop: "drop",
  layered_stack: "layered",
  hollow_out: "hollow",
  cutout: "hollow",
  engraved_detail: "engraved"
};

const styleAliases = {
  modern: "fashion",
  contemporary: "fashion",
  bridal_set: "bridal",
  wedding: "bridal",
  antique: "vintage",
  retro: "vintage",
  simple: "minimal",
  luxury_style: "luxury",
  geometric_style: "geometric",
  floral_style: "floral",
  chinese: "chinese_style"
};

const materialColorAliases = {
  gold: "yellow_gold",
  yellow: "yellow_gold",
  gold_yellow: "yellow_gold",
  yellowgold: "yellow_gold",
  yellow_gold_color: "yellow_gold",
  white_gold: "white",
  white_gold_color: "white",
  silver: "white",
  silver_color: "white",
  platinum: "white",
  platinum_color: "white",
  rose: "rose_gold",
  pink_gold: "rose_gold",
  rosegold: "rose_gold",
  rose_gold_color: "rose_gold",
  black_gold: "black",
  black_rhodium: "black",
  two_color: "two_tone",
  two_tone_metal: "two_tone",
  yellow_white: "two_tone",
  white_yellow: "two_tone",
  mixed_metal: "two_tone",
  unclear: "unclear",
  none: "unclear",
  unknown: "unclear"
};

const splitPattern = /[,\u3001/|+&]+|\s+and\s+/i;

function uniq(values) {
  return [...new Set(values.filter(Boolean))];
}

function toSlug(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\w\s,/|+&-]+/g, " ")
    .replace(/[-\s]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function splitValues(value) {
  if (Array.isArray(value)) {
    return value.flatMap((item) => splitValues(item));
  }
  const raw = String(value || "").trim();
  if (!raw) return [];
  return raw
    .split(splitPattern)
    .map((item) => toSlug(item))
    .filter(Boolean);
}

function normalizeKeywordList(value) {
  return uniq(splitValues(value));
}

function normalizeEnumList(value, allowedValues, aliases = {}) {
  const allowed = new Set(allowedValues);
  const normalized = [];
  for (const part of splitValues(value)) {
    const canonical = aliases[part] || part;
    if (allowed.has(canonical)) {
      normalized.push(canonical);
    }
  }
  return uniq(normalized);
}

function normalizeScalarFromList(value, fallback, allowedValues, aliases = {}) {
  const normalized = normalizeEnumList(value, allowedValues, aliases);
  return normalized[0] || fallback;
}

export function emptyTags() {
  return {
    category: "unclear",
    stone_shape: "unclear",
    stone_shapes: [],
    settings: [],
    band_types: [],
    structures: [],
    styles: [],
    material_color: "unclear",
    material_colors: [],
    reuse_keywords: [],
    description: "",
    confidence: 0
  };
}

export function normalizeTags(input = {}) {
  const base = emptyTags();
  const tags = { ...base, ...input };

  const stoneShapes = normalizeEnumList(
    tags.stone_shapes?.length ? tags.stone_shapes : tags.stone_shape,
    taxonomy.stoneShapes,
    stoneShapeAliases
  ).filter((value) => value !== "unclear");
  const materialColors = normalizeEnumList(
    tags.material_colors?.length ? tags.material_colors : tags.material_color,
    taxonomy.materialColors,
    materialColorAliases
  ).filter((value) => value !== "unclear");

  tags.category = normalizeScalarFromList(tags.category, base.category, taxonomy.categories, categoryAliases);
  tags.stone_shapes = stoneShapes;
  tags.stone_shape = stoneShapes[0] || base.stone_shape;
  tags.settings = normalizeEnumList(tags.settings, taxonomy.settings, settingAliases);
  tags.band_types = normalizeEnumList(tags.band_types, taxonomy.bandTypes, bandTypeAliases);
  tags.structures = normalizeEnumList(tags.structures, taxonomy.structures, structureAliases);
  tags.styles = normalizeEnumList(tags.styles, taxonomy.styles, styleAliases);
  tags.material_colors = materialColors;
  tags.material_color =
    materialColors.length > 1 ? "two_tone" : materialColors[0] || base.material_color;
  tags.reuse_keywords = normalizeKeywordList(tags.reuse_keywords);
  tags.description = String(tags.description || "").trim();
  tags.confidence = Number.isFinite(Number(tags.confidence)) ? Number(tags.confidence) : 0;
  return tags;
}

export function tagTerms(tags = {}) {
  const normalized = normalizeTags(tags);
  return uniq([
    normalized.category,
    normalized.stone_shape,
    ...normalized.stone_shapes,
    normalized.material_color,
    ...normalized.material_colors,
    ...normalized.settings,
    ...normalized.band_types,
    ...normalized.structures,
    ...normalized.styles,
    ...normalized.reuse_keywords
  ]).filter((value) => value && value !== "unclear");
}

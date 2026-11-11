/** Text cleanup for narration. Port of b2a/clean.py (same rules, same order). */
const W = "[\\p{L}\\p{N}_]";                       // Python's \w
const B = `(?<!${W})`, E = `(?!${W})`;           // Python's \b around a word
const re = (src: string, flags = "u") => new RegExp(src, flags);

const HTML_COMMENT = /<!--.*?-->/gs;
const DATA_IMG = /!\[[^\]]*\]\(data:image\/[^)]+\)/g;
const CODE_FENCE = /```.*?```/gs;
const IMAGE = /!\[[^\]]*\]\([^)]*\)/g;
const LINK = /\[([^\]]+)\]\([^)]*\)/g;
const HTML_TAG = /<[^>]+>/g;
const URL_RE = /(?:https?:\/\/|www\.)\S+/g;
const FOOTNOTE = /\[\d+\]|\{\d+\}|\[\d+[a-z]?\]/g;
const MD_INLINE = /(\*\*\*|\*\*|\*|__|_|`)/g;
const MD_LIST_PREFIX = /^\s{0,6}(?:[-*+]\s+|\d+[.)]\s+|>\s?|#{1,6}\s+)/;
const PAGE_LINE = /^\s*#{0,6}\s*(?:page|p\.?)\s*\d+\s*$/i;
const HYPHEN_WRAP = re(`(${W}+)-\\n(${W}+)`, "gu");
const WS = /[ \t]+/g;
const TABLE_LINE = /^\s*\|.*\|\s*$/;

const ABBREV: [RegExp, string][] = [
  [re(`${B}Dr\\.(?=\\s)`, "giu"), "Doctor"], [re(`${B}Mr\\.(?=\\s)`, "giu"), "Mister"],
  [re(`${B}Mrs\\.(?=\\s)`, "giu"), "Missus"], [re(`${B}Ms\\.(?=\\s)`, "giu"), "Miss"],
  [re(`${B}e\\.g\\.,?`, "giu"), "for example,"], [re(`${B}i\\.e\\.,?`, "giu"), "that is,"],
  [re(`${B}etc\\.(?=\\s|$)`, "giu"), "etcetera"], [re(`${B}vs\\.?(?=\\s)`, "giu"), "versus"],
  [re(`${B}Fig\\.(?=\\s)`, "giu"), "Figure"],
  [re(`${B}St\\.(?=\\s[A-Z])`, "giu"), "Saint"],   // Python IGNORECASE also makes [A-Z] case-blind
];
const BOILERPLATE: RegExp[] = [
  /\b(all rights reserved|reproduced in any form|printed in the united states|isbn\s*[\d\-x]{10,18})\b/i,
  /\b(cataloging-in-publication data|library of congress|british library cataloguing)\b/i,
  /\b(published by|publishing group|typeset in|cover design by)\b/i,
  /^\s*(table of contents|contents)\s*$/i, /^\s*index\s*$/i,
  /^\s*(\.{3,}|_{3,}|-{3,})\s*$/, /^.{1,40}\.{3,}\s*\d{1,4}\s*$/,
];

export type Lexicon = Record<string, string>;
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function applyLexicon(text: string, lexicon: Lexicon): string {
  if (!text || !lexicon) return text;
  for (const [word, repl] of Object.entries(lexicon)) {
    const isUpper = word === word.toUpperCase() && word !== word.toLowerCase();
    text = text.replace(re(`${B}${escapeRe(word)}${E}`, isUpper ? "giu" : "gu"), () => repl);
  }
  return text;
}
export const isBoilerplate = (t: string) => !!t && BOILERPLATE.some((p) => p.test(t));

export function stripMarkdown(text: string): string {
  text = text.replace(HTML_COMMENT, " ").replace(DATA_IMG, " ").replace(CODE_FENCE, " ").replace(IMAGE, " ")
    .replace(LINK, "$1").replace(HTML_TAG, " ").replace(TABLE_LINE, " ").replace(MD_LIST_PREFIX, "").replace(MD_INLINE, "");
  return text.replace(WS, " ").trim();
}

export function cleanParagraphText(text: string, lexicon?: Lexicon): string {
  if (!text) return "";
  if (PAGE_LINE.test(text.trim())) return "";
  text = stripMarkdown(text);
  if (!text) return "";
  text = text.replace(URL_RE, " ").replace(FOOTNOTE, "");
  for (const [p, r] of ABBREV) text = text.replace(p, r);
  text = text.replace(HYPHEN_WRAP, "$1$2 ").replace(/­/g, "").replace(/\n/g, " ");
  text = text.replace(/\s*[—–]\s*/g, ", ").replace(/;\s*/g, ", ").replace(/\.{4,}/g, "...")
    .replace(/\s([,.;:!?])/g, "$1").replace(/([,;:])\s(?=[\w"'])/g, "$1 ").replace(/""/g, '"');
  text = text.replace(WS, " ").trim();
  if (isBoilerplate(text)) return "";
  if (/^(?:[^\p{L}\p{N}]|\p{Nd}|_)+$/u.test(text || " ")) return "";
  const chars = [...text];
  const letters = chars.filter((c) => /\p{L}/u.test(c)).length;
  if (letters < Math.max(4, Math.trunc(0.35 * chars.length))) return "";
  if (lexicon && Object.keys(lexicon).length) text = applyLexicon(text, lexicon);
  return text;
}
export const cleanParagraphs = (blocks: string[], lexicon?: Lexicon) =>
  blocks.map((b) => cleanParagraphText(b, lexicon)).filter(Boolean);

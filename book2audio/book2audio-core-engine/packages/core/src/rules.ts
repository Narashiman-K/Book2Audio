/** Rule-based prosody planner. Port of b2a/prosody/rules.py. */
import type { Chapter, ProsodySegment } from "./models.js";
import { getStyle } from "./styles.js";

const QUOTE = /[“"]([^”"]{3,})[”"]/;
const CAPS = /\b[A-Z]{3,}\b/g;
const ELLIPSIS = /(\.\s*\.\s*\.|…|\.\.\.)/;
const Q_END = /\?\s*["'”’)\]]?\s*$/;
const DASH = /\s[—–]\s/;
const CONTRAST = ["but","however","although","yet","only","never","always","instead"];
const NEGATION = ["not","neither","nowhere","cannot","hardly","scarcely"];
const SUPERLATIVE = ["best","worst","greatest","most","least","first","last","essential","crucial"];
const has = (lowered: string, w: string) => new RegExp(`(?<![\\p{L}\\p{N}_])${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}\\p{N}_])`, "u").test(lowered);

export interface RulesOptions { styleName?: string; powerWords?: string[]; maxEmphasisPerSentence?: number }
export class RulesEngine {
  private style; private power: string[]; private maxEmph: number;
  constructor(o: RulesOptions = {}) {
    this.style = getStyle(o.styleName ?? "warm");
    this.power = [...new Set((o.powerWords ?? []).map((w) => w.toLowerCase()))];
    this.maxEmph = o.maxEmphasisPerSentence ?? 2;
  }
  planChapter(c: Chapter): ProsodySegment[] { return c.paragraphs.map((p) => this.planParagraph(p.text, p.id)); }
  planParagraph(text: string, paraId: string): ProsodySegment {
    const st = this.style;
    const seg: ProsodySegment = { paraId, mood: "neutral", emphasis: [], rateDelta: st.baseRateDelta, pitchDelta: st.basePitchDelta, pauseAfterMs: 400, source: "rules" };
    const quote = QUOTE.exec(text);
    if (quote) { seg.mood = "dialogue"; seg.pitchDelta += 2; }
    else if (Q_END.test(text)) { seg.mood = "question"; seg.pitchDelta += 2; seg.rateDelta -= 2; }
    else if (text.includes("!")) { seg.mood = "excited"; seg.rateDelta += 4; seg.pitchDelta += 3; }
    else if (ELLIPSIS.test(text) || ((text.trimEnd().endsWith(".") || text.trimEnd().endsWith("…")) && [...text].length > 240)) { seg.mood = "reflective"; seg.rateDelta -= 4; }

    const picked: string[] = [], seen = new Set<string>(), lowered = text.toLowerCase(), cap = this.maxEmph * 2;
    for (const m of text.matchAll(CAPS)) { const w = m[0].toLowerCase(); if (!seen.has(w) && w.length > 3) { picked.push(m[0]); seen.add(w); } }
    for (const w of this.power) { if (picked.length >= cap) break; if (has(lowered, w) && !seen.has(w)) { picked.push(w); seen.add(w); } }
    for (const set of [CONTRAST, NEGATION, SUPERLATIVE]) {
      if (picked.length >= cap) break;
      for (const w of set) { if (picked.length >= cap) break; if (has(lowered, w) && !seen.has(w)) { picked.push(w); seen.add(w); } }
    }
    if (quote && quote[1]!.split(/\s+/).filter(Boolean).length <= 4) picked.push(quote[1]!);
    seg.emphasis = picked.slice(0, Math.max(this.maxEmph, 1) + 1);

    const ps = st.pauseScale;
    if (ELLIPSIS.test(text)) seg.pauseAfterMs = Math.trunc(800 * ps);
    else if (DASH.test(text)) seg.pauseAfterMs = Math.trunc(600 * ps);
    else if (seg.mood === "dialogue") seg.pauseAfterMs = Math.trunc(550 * ps);
    else if (seg.mood === "excited") seg.pauseAfterMs = Math.trunc(450 * ps);
    else seg.pauseAfterMs = Math.trunc(420 * ps);

    seg.rateDelta = Math.max(-st.maxRateDelta, Math.min(st.maxRateDelta, seg.rateDelta)) || 0;
    seg.pitchDelta = Math.max(-st.maxPitchDelta, Math.min(st.maxPitchDelta, seg.pitchDelta)) || 0;
    seg.pauseAfterMs = Math.max(0, Math.min(1500, seg.pauseAfterMs));
    return seg;
  }
}

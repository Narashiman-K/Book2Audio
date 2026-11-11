import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { cleanParagraphText, splitSentences, RulesEngine } from "../src/index.js";

const fx = JSON.parse(readFileSync(new URL("./fixtures/reference.json", import.meta.url), "utf8"));

describe("clean: same output as the Python version", () => {
  fx.clean.forEach((c: any, i: number) => {
    it(`case ${i}: ${JSON.stringify(c.in).slice(0, 40)}`, () => {
      expect(cleanParagraphText(c.in, fx.lexicon)).toBe(c.out);
      expect(cleanParagraphText(c.in)).toBe(c.out_nolex);
    });
  });
});
describe("segment: same sentences as the Python version", () => {
  fx.sentences.forEach((c: any, i: number) => {
    it(`case ${i}`, () => { expect(splitSentences(c.in)).toEqual(c.out); expect(splitSentences(c.in, 60)).toEqual(c.out_60); });
  });
});
describe("prosody rules: same plan as the Python version", () => {
  for (const [style, cases] of Object.entries<any[]>(fx.rules)) {
    const eng = new RulesEngine({ styleName: style, powerWords: fx.power_words, maxEmphasisPerSentence: fx.max_emph });
    cases.forEach((c, i) => it(`${style} ${i}`, () => {
      const s = eng.planParagraph(c.text, "p");
      expect({ mood: s.mood, rate: s.rateDelta, pitch: s.pitchDelta, pause: s.pauseAfterMs, n: s.emphasis.length })
        .toEqual({ mood: c.mood, rate: c.rate_delta, pitch: c.pitch_delta, pause: c.pause_after_ms, n: c.emphasis.length });
      // Python picks power words in random set order when more candidates exist than slots, so the exact
      // words can legitimately differ; the count (checked above) and presence in the text must match.
      const a = [...s.emphasis].sort(), b = [...c.emphasis].sort();
      if (JSON.stringify(a) !== JSON.stringify(b)) a.forEach((w) => expect(c.text.toLowerCase()).toContain(w.toLowerCase()));
    }));
  }
});

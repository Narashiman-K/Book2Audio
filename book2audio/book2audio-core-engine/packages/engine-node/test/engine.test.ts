import { describe, it, expect } from "vitest";
import { SilentEngine } from "@b2a/core";
import { floatToWav, synthesizeParagraph } from "../src/index.js";
import { KokoroNodeEngine } from "../src/kokoro.js";

describe("wav + paragraph synthesis", () => {
  it("writes a valid WAV header", () => {
    const w = floatToWav(new Float32Array(2400), 24000);
    expect(w.toString("ascii", 0, 4)).toBe("RIFF"); expect(w.readUInt32LE(24)).toBe(24000); expect(w.length).toBe(44 + 4800);
  });
  it("joins sentences with gaps", async () => {
    const r = await synthesizeParagraph(new SilentEngine(), "One short sentence. Another short sentence here.", { voice: "af_heart", speed: 1, gapSec: 0.5 });
    expect(r.sampleRate).toBe(24000); expect(r.samples.length).toBeGreaterThan(24000 * 0.5);
  });
});
// Real voice smoke test: only runs when asked (needs the model; ~1.5 s to load).
const real = process.env.B2A_REAL_KOKORO ? describe : describe.skip;
real("Kokoro in Node", () => {
  it("speaks", async () => {
    const e = new KokoroNodeEngine({ modelDir: process.env.B2A_MODEL_DIR ?? "models" });
    const v = await e.listVoices(); expect(v.some((x) => x.id === "af_heart")).toBe(true);
    const r = await synthesizeParagraph(e, "Walking lets the mind wander. Many writers took long daily walks.", { voice: "af_heart", speed: 1 });
    expect(r.samples.length / r.sampleRate).toBeGreaterThan(3);
  }, 120000);
});

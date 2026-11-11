import { splitSentences, type TtsEngine, type SynthOptions, type SynthResult } from "@b2a/core";
import { concatSamples, silence } from "./wav.js";

/** Speak a whole paragraph: split into sentences (the model has a length limit), speak each, join with a short gap. */
export async function synthesizeParagraph(engine: TtsEngine, text: string, opts: SynthOptions & { gapSec?: number; maxChars?: number }): Promise<SynthResult> {
  const parts = splitSentences(text, opts.maxChars ?? 300), gap = opts.gapSec ?? 0.14;
  if (!parts.length) return { samples: new Float32Array(0), sampleRate: 24000 };
  const pieces: Float32Array[] = []; let sr = 24000;
  for (let i = 0; i < parts.length; i++) {
    const r = await engine.synthesize(parts[i]!, opts); sr = r.sampleRate;
    pieces.push(r.samples); if (i < parts.length - 1) pieces.push(silence(gap, sr));
  }
  return { samples: concatSamples(pieces), sampleRate: sr };
}

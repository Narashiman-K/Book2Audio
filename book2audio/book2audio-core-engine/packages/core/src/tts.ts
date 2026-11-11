/** The one interface every voice engine implements (Kokoro on Node, Kokoro in the browser, a silent test engine). */
export interface Voice { id: string; label: string; lang?: string; gender?: string }
export interface SynthOptions { voice: string; speed: number }
export interface SynthResult { samples: Float32Array; sampleRate: number }
export interface TtsEngine {
  readonly name: string;
  listVoices(): Promise<Voice[]>;
  synthesize(text: string, opts: SynthOptions): Promise<SynthResult>;
  dispose?(): Promise<void>;
}
/** Test engine: silence whose length follows the text, like the Python SilentTTS. */
export class SilentEngine implements TtsEngine {
  readonly name = "silent";
  async listVoices(): Promise<Voice[]> { return [{ id: "af_heart", label: "af_heart (silent test voice)" }]; }
  async synthesize(text: string, opts: SynthOptions): Promise<SynthResult> {
    const sr = 24000, sec = Math.max(0.3, text.length / 15 / (opts.speed || 1));
    return { samples: new Float32Array(Math.round(sr * sec)), sampleRate: sr };
  }
}

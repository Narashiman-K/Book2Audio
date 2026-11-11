import { existsSync } from "node:fs";
import { join } from "node:path";
import type { TtsEngine, Voice, SynthOptions, SynthResult } from "@b2a/core";

export const KOKORO_MODEL_ID = "onnx-community/Kokoro-82M-v1.0-ONNX";
export interface KokoroNodeOptions {
  /** Folder holding the model (onnx-community/Kokoro-82M-v1.0-ONNX/...). Used offline when present, else it is downloaded here once. */
  modelDir: string;
  dtype?: "fp32" | "fp16" | "q8" | "q4" | "q4f16";
}

/** Kokoro-82M running in Node on the CPU through onnxruntime-node (via kokoro-js). */
export class KokoroNodeEngine implements TtsEngine {
  readonly name = "kokoro";
  private tts: any = null;
  private loading: Promise<any> | null = null;
  constructor(private opts: KokoroNodeOptions) {}

  /** True when the model files are already on disk, so loading needs no internet. */
  hasLocalModel(): boolean {
    return existsSync(join(this.opts.modelDir, ...KOKORO_MODEL_ID.split("/"), "config.json"));
  }
  private async load() {
    if (this.tts) return this.tts;
    this.loading ??= (async () => {
      const { env } = await import("@huggingface/transformers");
      const { KokoroTTS } = await import("kokoro-js");
      if (this.hasLocalModel()) { env.localModelPath = this.opts.modelDir; env.allowRemoteModels = false; env.allowLocalModels = true; }
      else { env.cacheDir = this.opts.modelDir; }
      this.tts = await KokoroTTS.from_pretrained(KOKORO_MODEL_ID, { dtype: this.opts.dtype ?? "q8", device: "cpu" });
      return this.tts;
    })();
    try { return await this.loading; } catch (e) { this.loading = null; throw e; }
  }
  async ready(): Promise<{ ok: boolean; error: string }> {
    try { await this.load(); return { ok: true, error: "" }; } catch (e: any) { return { ok: false, error: String(e?.message ?? e) }; }
  }
  async listVoices(): Promise<Voice[]> {
    const tts = await this.load();
    return Object.entries<any>(tts.voices).map(([id, v]) => ({
      id, label: `${id} (${v.name}, ${v.language === "en-us" ? "US" : "UK"} ${v.gender.toLowerCase()})`, lang: v.language, gender: v.gender.toLowerCase(),
    }));
  }
  async synthesize(text: string, o: SynthOptions): Promise<SynthResult> {
    const tts = await this.load();
    const voice = tts.voices[o.voice] ? o.voice : "af_heart";
    const speed = Math.max(0.5, Math.min(2, o.speed || 1));
    const a = await tts.generate(text, { voice, speed });
    return { samples: a.audio as Float32Array, sampleRate: a.sampling_rate as number };
  }
  async dispose() { this.tts = null; this.loading = null; }
}

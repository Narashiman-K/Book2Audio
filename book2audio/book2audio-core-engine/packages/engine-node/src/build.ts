/** Book build: chapters -> normalised MP3s (+ master WAVs) -> optional chaptered M4B + sidecar timeline.
 *  Port of b2a/pipeline.py. Finished chapters are reused (resume); Stop keeps what is done. */
import { createHash } from "node:crypto";
import { mkdirSync, existsSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import path from "node:path";
import { RulesEngine, type Book, type Chapter, type TtsEngine } from "@b2a/core";
import { concatSamples, floatToWav, silence } from "./wav.js";
import { synthesizeParagraph } from "./synth.js";
import { BuildCancelled, normalise } from "./ffmpeg.js";
import { assembleM4b, type BookMeta, type ChapterFile } from "./m4b.js";


export interface BuildOptions {
  book: Book; outRoot: string; engine: TtsEngine;
  voice?: string; speed?: number; style?: string; powerWords?: string[];
  chapters?: number[];              // chapter indexes to build (default: all)
  mp3?: boolean; m4b?: boolean;     // default mp3 true, m4b true
  mp3Bitrate?: string; m4bBitrate?: string; lufs?: number; tempo?: number;
  leadSilenceMs?: number; cover?: string; meta?: Partial<BookMeta>; chapterTitles?: Record<string, string>;
  force?: boolean; ffmpeg?: string; signal?: AbortSignal;
  onProgress?: (e: BuildProgress) => void;
}
export interface BuildProgress { phase: "chapter" | "m4b" | "done"; chapter?: number; fraction: number; message: string }
export interface BuildResult { outDir: string; chapters: ChapterFile[]; m4b?: string; sidecar?: string; reused: number[]; stopped?: boolean }

export const slugify = (s: string) => (s.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "").trim().replace(/\s+/g, "-").slice(0, 60).replace(/[-. ]+$/, "")) || "book";
const safe = (t: string) => t.replace(/<[^>]+>/g, "").replace(/[<>:"/\\|?*]/g, "").trim() || "Chapter";
const sha = (s: string) => createHash("sha1").update(s).digest("hex").slice(0, 16);

export async function buildBook(o: BuildOptions): Promise<BuildResult> {
  const { book } = o, voice = o.voice ?? "af_heart", speed = o.speed ?? 1, tempo = o.tempo ?? 1;
  const lead = o.leadSilenceMs ?? 1200, lufs = o.lufs ?? -19, doMp3 = o.mp3 !== false, doM4b = o.m4b !== false;
  const outDir = path.join(o.outRoot, slugify(book.title)), buildDir = path.join(o.outRoot, "_build", slugify(book.title) + "-" + sha(book.sourcePath || book.title).slice(0, 8));
  mkdirSync(outDir, { recursive: true }); mkdirSync(buildDir, { recursive: true });
  const want = new Set(o.chapters ?? book.chapters.map((c) => c.index));
  const chosen = book.chapters.filter((c) => want.has(c.index));
  const rules = new RulesEngine({ styleName: o.style ?? "warm", powerWords: o.powerWords });
  const say = (p: BuildProgress) => o.onProgress?.(p);
  const results: ChapterFile[] = [], reused: number[] = [], timelines = new Map<number, Record<string, [number, number]>>();
  const stopped = () => o.signal?.aborted === true;

  for (let i = 0; i < chosen.length; i++) {
    const ch = chosen[i]!;
    if (stopped()) return finish(true);
    const title = o.chapterTitles?.[String(ch.index)] || ch.title;
    const base = `${String(ch.index).padStart(2, "0")} - ${safe(title)}`;
    const mp3 = path.join(outDir, base + ".mp3"), wav = path.join(buildDir, `chapter_${String(ch.index).padStart(2, "0")}.wav`);
    const tlFile = path.join(buildDir, `timeline_c${String(ch.index).padStart(2, "0")}.json`), keyFile = path.join(buildDir, `key_c${String(ch.index).padStart(2, "0")}.txt`);
    const key = sha(JSON.stringify([ch.paragraphs.map((p) => p.text), voice, speed, o.style ?? "warm", o.powerWords ?? [], lufs, tempo, lead, o.engine.name]));
    const done = !o.force && existsSync(wav) && existsSync(tlFile) && existsSync(keyFile) && readFileSync(keyFile, "utf8") === key && (!doMp3 || existsSync(mp3));
    const frac = (f: number) => (i + f) / chosen.length * (doM4b ? 0.9 : 1);
    if (done) {
      reused.push(ch.index); say({ phase: "chapter", chapter: ch.index, fraction: frac(1), message: `Chapter ${ch.index} already built, reusing` });
    } else {
      const tl = await renderChapter(o, ch, rules, { voice, speed, lead, wav, mp3: doMp3 ? mp3 : "", lufs, tempo, frac: (f, m) => say({ phase: "chapter", chapter: ch.index, fraction: frac(f), message: m }) });
      writeFileSync(tlFile, JSON.stringify(tl)); writeFileSync(keyFile, key);
    }
    timelines.set(ch.index, JSON.parse(readFileSync(tlFile, "utf8")));
    results.push({ chapter: ch.index, title, file: mp3, wavFile: wav });
  }
  return finish(false);

  async function finish(wasStopped: boolean): Promise<BuildResult> {
    const out: BuildResult = { outDir, chapters: results, reused, stopped: wasStopped };
    if (wasStopped || !results.length) return out;
    if (doM4b) {
      const idx = results.map((r) => r.chapter), full = idx.length === book.chapters.length, slug = slugify(book.title);
      const contiguous = idx.every((v, k) => k === 0 || v === idx[k - 1]! + 1);
      const m4b = path.join(outDir, full ? `${slug}.m4b` : contiguous ? `${slug} (ch ${idx[0]}-${idx[idx.length - 1]}).m4b` : `${slug} (selected chapters).m4b`);
      const meta: BookMeta = { title: book.title, author: book.author, narrator: o.engine.name === "kokoro" ? `Kokoro ${voice}` : voice, ...stripEmpty(o.meta) };
      say({ phase: "m4b", fraction: 0.9, message: `Assembling ${path.basename(m4b)} ...` });
      const cover = o.cover && existsSync(o.cover) ? o.cover : undefined;
      const r = await assembleM4b({ chapters: results, out: m4b, meta, bitrate: o.m4bBitrate ?? "96k", cover, ffmpeg: o.ffmpeg, signal: o.signal, onProgress: (f) => say({ phase: "m4b", fraction: 0.9 + 0.1 * f, message: "Assembling M4B" }) });
      const scale = tempo && Math.abs(tempo - 1) > 0.01 ? 1 / tempo : 1, timeline: Record<string, [number, number]> = {};
      r.marks.forEach((mk, k) => { const tl = timelines.get(results[k]!.chapter)!; for (const [id, [s, e]] of Object.entries(tl)) timeline[id] = [+(mk.start + s * scale).toFixed(2), +Math.min(mk.end, mk.start + e * scale).toFixed(2)]; });
      const sidecar = m4b.replace(/\.m4b$/i, ".b2a.json");
      writeFileSync(sidecar, JSON.stringify({ version: 1, m4b: path.basename(m4b), meta, settings: { voice, speed, style: o.style ?? "warm", bitrate: o.m4bBitrate ?? "96k" }, cover: r.cover ? path.basename(cover!) : "", duration: r.marks[r.marks.length - 1]!.end, chapters: r.marks.map((m, k) => ({ ...m, mp3: path.basename(results[k]!.file) })), timeline, built: new Date().toISOString() }, null, 1));
      out.m4b = m4b; out.sidecar = sidecar;
    }
    writeFileSync(path.join(outDir, "manifest.json"), JSON.stringify({ title: book.title, author: book.author, voice, chapters: results.map((r) => ({ chapter: r.chapter, title: r.title, file: path.basename(r.file) })), m4b_file: out.m4b ?? "" }, null, 2));
    say({ phase: "done", fraction: 1, message: "Done" });
    return out;
  }
}
const stripEmpty = (m?: Partial<BookMeta>) => Object.fromEntries(Object.entries(m ?? {}).filter(([, v]) => v !== undefined && v !== "")) as Partial<BookMeta>;

interface RenderOpts { voice: string; speed: number; lead: number; wav: string; mp3: string; lufs: number; tempo: number; frac: (f: number, msg: string) => void }
/** Speak every paragraph, join with the planned pauses, normalise loudness, write MP3 + master WAV. Returns paragraph timings (s). */
async function renderChapter(o: BuildOptions, ch: Chapter, rules: RulesEngine, r: RenderOpts): Promise<Record<string, [number, number]>> {
  const plan = rules.planChapter(ch), pieces: Float32Array[] = [], tl: Record<string, [number, number]> = {};
  let sr = 24000, cur = 0;
  pieces.push(silence(r.lead / 1000, sr)); cur = r.lead / 1000;
  for (let k = 0; k < ch.paragraphs.length; k++) {
    if (o.signal?.aborted) throw new BuildCancelled();
    const p = ch.paragraphs[k]!, seg = plan[k]!;
    const sp = Math.max(0.5, Math.min(2, r.speed * (1 + seg.rateDelta / 100)));
    const res = await synthesizeParagraph(o.engine, p.text, { voice: r.voice, speed: sp });
    sr = res.sampleRate;
    const start = cur, dur = res.samples.length / sr; cur += dur;
    pieces.push(res.samples); tl[p.id] = [+start.toFixed(2), +cur.toFixed(2)];
    const pause = seg.pauseAfterMs / 1000; if (pause > 0) { pieces.push(silence(pause, sr)); cur += pause; }
    r.frac((k + 1) / ch.paragraphs.length * 0.8, `Chapter ${ch.index}: paragraph ${k + 1}/${ch.paragraphs.length}`);
  }
  const raw = r.wav.replace(/\.wav$/, ".raw.wav"); writeFileSync(raw, floatToWav(concatSamples(pieces), sr));
  r.frac(0.85, `Chapter ${ch.index}: levelling volume`);
  await normalise(raw, r.wav, { lufs: r.lufs, tempo: r.tempo, codecArgs: ["-c:a", "pcm_s16le"], ffmpeg: o.ffmpeg, signal: o.signal });
  if (r.mp3) {
    await normalise(raw, r.mp3, { lufs: r.lufs, tempo: r.tempo, codecArgs: ["-codec:a", "libmp3lame", "-b:a", o.mp3Bitrate ?? "96k"], metadata: ["-metadata", `title=${String(ch.index).padStart(2, "0")} ${ch.title}`, "-metadata", `album=${o.book.title}`, "-metadata", `artist=${o.book.author}`, "-metadata", `track=${ch.index}/${o.book.chapters.length}`, "-metadata", "comment=Made with book2audio - personal/family use only"], ffmpeg: o.ffmpeg, signal: o.signal });
  }
  rmSync(raw, { force: true });
 
  r.frac(1, `Chapter ${ch.index} done`);
  return tl;
}

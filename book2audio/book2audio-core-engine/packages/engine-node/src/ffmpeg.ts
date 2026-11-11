/** Thin ffmpeg/ffprobe helpers (Node). Port of the helpers in b2a/audio.py. */
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

const works = (bin: string) => { try { return spawnSync(bin, ["-version"], { timeout: 8000 }).status === 0; } catch { return false; } };
let cached: string | undefined;

/** Find a working ffmpeg: explicit path, B2A_FFMPEG_PATH, PATH, then common Windows folders. */
export function findFfmpeg(explicit?: string): string {
  if (explicit && works(explicit)) return explicit;
  if (cached && !explicit) return cached;
  const exe = process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg";
  const cands = [process.env.B2A_FFMPEG_PATH ?? "", "ffmpeg"];
  for (const pf of [process.env.ProgramFiles, process.env["ProgramFiles(x86)"]]) {
    if (pf) cands.push(path.join(pf, "ffmpeg", "bin", exe), path.join(pf, "ffmpeg", exe));
  }
  for (const c of cands) if (c && (c === "ffmpeg" || existsSync(c)) && works(c)) return (cached = c);
  throw new Error("ffmpeg was not found. Install it (winget install Gyan.FFmpeg) or set B2A_FFMPEG_PATH.");
}
const probeBin = (ff: string) => (ff === "ffmpeg" ? "ffprobe" : path.join(path.dirname(ff), path.basename(ff).replace(/ffmpeg/i, "ffprobe")));

export function runFfmpeg(args: string[], opts: { ffmpeg?: string; signal?: AbortSignal; onProgress?: (outSec: number) => void } = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    const full = ["-hide_banner", "-loglevel", "error", "-nostats", "-y", ...(opts.onProgress ? ["-progress", "pipe:1"] : []), ...args];
    const p = spawn(findFfmpeg(opts.ffmpeg), full, { windowsHide: true });
    let err = "", buf = "";
    const onAbort = () => { p.kill(); };
    opts.signal?.addEventListener("abort", onAbort, { once: true });
    p.stdout.on("data", (d) => {
      if (!opts.onProgress) return;
      buf += d.toString(); const lines = buf.split(/\r?\n/); buf = lines.pop() ?? "";
      for (const l of lines) if (l.startsWith("out_time_us=")) { const v = Number(l.slice(12)); if (Number.isFinite(v)) opts.onProgress(v / 1e6); }
    });
    p.stderr.on("data", (d) => { err += d.toString(); });
    p.on("error", reject);
    p.on("close", (code) => {
      opts.signal?.removeEventListener("abort", onAbort);
      if (opts.signal?.aborted) return reject(new BuildCancelled());
      code === 0 ? resolve(err) : reject(new Error(`ffmpeg failed (code ${code}): ${err.trim().slice(-800)}`));
    });
  });
}

export class BuildCancelled extends Error { constructor() { super("Build stopped"); this.name = "BuildCancelled"; } }

export function probeDuration(file: string, ffmpeg?: string): number {
  const r = spawnSync(probeBin(findFfmpeg(ffmpeg)), ["-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file], { encoding: "utf8", timeout: 60000 });
  const v = Number((r.stdout || "").trim());
  return Number.isFinite(v) ? v : 0;
}

/** Two-pass EBU R128 loudness normalisation (same filter chain as Python) and encode. */
export async function normalise(inWav: string, out: string, o: { lufs: number; tempo?: number; codecArgs: string[]; metadata?: string[]; ffmpeg?: string; signal?: AbortSignal }): Promise<void> {
  let filters = ["highpass=f=90", `loudnorm=I=${o.lufs}:TP=-1.5:LRA=11`];
  try {
    const log = await runFfmpegRaw(["-i", inWav, "-af", `highpass=f=90,loudnorm=I=${o.lufs}:TP=-1.5:LRA=11:print_format=json`, "-f", "null", "-"], o.ffmpeg, o.signal);
    const m = /\{\s*"input_i"[\s\S]*?\}/.exec(log);
    if (m) {
      const s = JSON.parse(m[0]); const ok = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"].every((k) => Number.isFinite(Number(s[k])));
      if (ok && Number(s.input_i) >= -99 && Number(s.input_i) <= 0)
        filters = ["highpass=f=90", `loudnorm=I=${o.lufs}:TP=-1.5:LRA=11:measured_I=${s.input_i}:measured_TP=${s.input_tp}:measured_LRA=${s.input_lra}:measured_thresh=${s.input_thresh}:offset=${s.target_offset}:linear=true`];
    }
  } catch (e) { if (e instanceof BuildCancelled) throw e; }
  if (o.tempo && Math.abs(o.tempo - 1) > 0.01) filters.push(`atempo=${Math.max(0.5, Math.min(2, o.tempo))}`);
  await runFfmpeg(["-i", inWav, "-af", filters.join(","), "-ar", "24000", "-ac", "1", ...o.codecArgs, ...(o.metadata ?? []), out], { ffmpeg: o.ffmpeg, signal: o.signal });
}

function runFfmpegRaw(args: string[], ffmpeg?: string, signal?: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const p = spawn(findFfmpeg(ffmpeg), ["-hide_banner", "-nostats", "-y", ...args], { windowsHide: true });
    let err = ""; p.stderr.on("data", (d) => { err += d.toString(); }); p.on("error", reject);
    signal?.addEventListener("abort", () => p.kill(), { once: true });
    p.on("close", () => (signal?.aborted ? reject(new BuildCancelled()) : resolve(err)));
  });
}

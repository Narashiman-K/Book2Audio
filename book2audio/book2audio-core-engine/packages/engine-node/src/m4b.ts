/** M4B assembly: chapter marks, cover, tags, no half-written files. Port of assemble_m4b / retag_m4b. */
import { mkdtempSync, rmSync, writeFileSync, existsSync, statSync, renameSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { runFfmpeg, probeDuration } from "./ffmpeg.js";

export interface BookMeta { title: string; author: string; narrator?: string; album?: string; year?: string; genre?: string; description?: string; publisher?: string }
export interface ChapterFile { chapter: number; title: string; file: string; wavFile?: string }
export interface Mark { index: number; title: string; start: number; end: number }

const esc = (v: string) => (v ?? "").replace(/\r/g, " ").replace(/([=;#\\\n])/g, "\\$1");
const clean = (t: string) => t.replace(/<[^>]+>/g, "").trim();

export function metaHeader(m: BookMeta): string[] {
  const title = m.title || "Audiobook", desc = m.description || "Made with book2audio - personal/family use only";
  const L = [";FFMETADATA1", `title=${esc(title)}`, `artist=${esc(m.author || "")}`, `album_artist=${esc(m.author || "")}`, `album=${esc(m.album || title)}`, `genre=${esc(m.genre || "Audiobook")}`, "media_type=2"];
  if (m.narrator) L.push(`composer=${esc(m.narrator)}`);
  if (m.year) L.push(`date=${esc(String(m.year))}`);
  if (m.publisher) L.push(`publisher=${esc(m.publisher)}`);
  L.push(`description=${esc(desc)}`, `comment=${esc(desc)}`);
  return L;
}
export function chapterLines(marks: Mark[]): string[] {
  const out: string[] = [];
  for (const m of marks) out.push("[CHAPTER]", "TIMEBASE=1/1000", `START=${Math.round(m.start * 1000)}`, `END=${Math.round(m.end * 1000)}`, `title=${esc(m.title)}`, "");
  return out;
}
/** Chapter marks measured from the exact files that get joined (never from MP3 duration: encoder padding makes it drift). */
export function computeMarks(chs: ChapterFile[], ffmpeg?: string): { marks: Mark[]; sources: string[] } {
  const useWav = chs.every((c) => c.wavFile && existsSync(c.wavFile));
  const marks: Mark[] = [], sources: string[] = []; let cur = 0;
  for (const c of chs) {
    const src = useWav ? c.wavFile! : c.file, dur = probeDuration(src, ffmpeg);
    if (dur <= 0) throw new Error(`cannot read duration of ${path.basename(src)}`);
    marks.push({ index: c.chapter, title: clean(c.title) || `Chapter ${c.chapter}`, start: +cur.toFixed(3), end: +(cur + dur).toFixed(3) });
    sources.push(src); cur += dur;
  }
  return { marks, sources };
}
const concatLine = (p: string) => `file '${path.resolve(p).replace(/\\/g, "/").replace(/'/g, "'\\''")}'`;

export async function prepareCover(src: string, dst: string, ffmpeg?: string, maxPx = 1400): Promise<string | null> {
  try {
    await runFfmpeg(["-i", src, "-vf", `scale='min(${maxPx},iw)':-2`, "-frames:v", "1", "-q:v", "2", "-pix_fmt", "yuvj420p", dst], { ffmpeg });
    return existsSync(dst) && statSync(dst).size > 0 ? dst : null;
  } catch { return null; }
}
function replaceFile(src: string, dst: string) {
  for (let i = 0; ; i++) {
    try { renameSync(src, dst); return; }
    catch (e) { if (i >= 7) throw new Error(`${path.basename(dst)} is in use by another program (a player?). Close it and try again.`); Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000); }
  }
}

export interface M4bOptions { chapters: ChapterFile[]; out: string; meta: BookMeta; bitrate?: string; cover?: string; ffmpeg?: string; signal?: AbortSignal; onProgress?: (f: number) => void }
export async function assembleM4b(o: M4bOptions): Promise<{ marks: Mark[]; cover: boolean }> {
  if (!o.chapters.length) throw new Error("no chapters to assemble into M4B");
  const { marks, sources } = computeMarks(o.chapters, o.ffmpeg), total = marks[marks.length - 1]!.end;
  mkdirSync(path.dirname(o.out), { recursive: true });
  const tmpOut = o.out.replace(/\.m4b$/i, "") + ".partial.m4b", td = mkdtempSync(path.join(tmpdir(), "b2a-m4b-"));
  try {
    writeFileSync(path.join(td, "concat.txt"), sources.map(concatLine).join("\n"), "utf8");
    writeFileSync(path.join(td, "meta.txt"), [...metaHeader(o.meta), "", ...chapterLines(marks)].join("\n"), "utf8");
    const cover = o.cover && existsSync(o.cover) ? await prepareCover(o.cover, path.join(td, "cover.jpg"), o.ffmpeg) : null;
    const args = ["-f", "concat", "-safe", "0", "-i", path.join(td, "concat.txt"), "-i", path.join(td, "meta.txt")];
    if (cover) args.push("-i", cover, "-map", "0:a", "-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"); else args.push("-map", "0:a", "-vn");
    args.push("-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", o.bitrate ?? "96k", "-ac", "1", "-metadata:s:a:0", "language=eng", "-movflags", "+faststart", "-f", "ipod", tmpOut);
    await runFfmpeg(args, { ffmpeg: o.ffmpeg, signal: o.signal, onProgress: (s) => o.onProgress?.(Math.min(1, s / total)) });
    if (!existsSync(tmpOut) || statSync(tmpOut).size === 0) throw new Error("ffmpeg produced an empty M4B");
    replaceFile(tmpOut, o.out);
    return { marks, cover: !!cover };
  } catch (e) { rmSync(tmpOut, { force: true }); throw e; }
  finally { rmSync(td, { recursive: true, force: true }); }
}

/** Rewrite tags, chapter titles and cover on an existing M4B without re-encoding. */
export async function retagM4b(m4b: string, meta: BookMeta, marks: Mark[], cover?: string, ffmpeg?: string): Promise<void> {
  const tmpOut = m4b.replace(/\.m4b$/i, "") + ".retag.m4b", td = mkdtempSync(path.join(tmpdir(), "b2a-tag-"));
  try {
    writeFileSync(path.join(td, "meta.txt"), [...metaHeader(meta), "", ...chapterLines(marks)].join("\n"), "utf8");
    const c = cover && existsSync(cover) ? await prepareCover(cover, path.join(td, "cover.jpg"), ffmpeg) : null;
    const args = ["-i", m4b, "-i", path.join(td, "meta.txt")];
    if (c) args.push("-i", c, "-map", "0:a", "-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"); else args.push("-map", "0:a", "-map", "0:v?", "-c:v", "copy");
    args.push("-map_metadata", "1", "-map_chapters", "1", "-c:a", "copy", "-movflags", "+faststart", "-f", "ipod", tmpOut);
    await runFfmpeg(args, { ffmpeg });
    replaceFile(tmpOut, m4b);
  } catch (e) { rmSync(tmpOut, { force: true }); throw e; }
  finally { rmSync(td, { recursive: true, force: true }); }
}

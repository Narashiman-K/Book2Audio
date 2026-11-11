/** Sentence segmentation for narration. Port of b2a/segment.py. */
const ABBR = new Set(["mr","mrs","ms","dr","prof","st","sr","jr","vs","etc","e.g","i.e","fig","no","vol","ch","pp","p","approx","mt","ft","capt","lt","sgt","col","gen","rev","hon","esq","ltd","co","inc","dept","univ","assn","bro","fr","cf","al","ed","eds","trans"]);
const SENT_END = /([.!?]+["'”’)\]]?)\s+/;
const CLAUSE = /(?<=[;:—–])(\s+)|(?<=,)\s+(?=(?:but|yet|however|although|though|while|whereas|and then|so that)\b)/i;

function protect(text: string): string {
  let o = text.replace(/\.\s*\.\s*\.|…/g, "\x01");
  o = o.replace(/\b(?:[A-Za-z]\.)+[A-Za-z]?\.?/g, (m) => m.replace(/\./g, "\x00"));
  o = o.replace(/\d\.\d/g, (m) => m.replace(/\./g, "\x00"));
  o = o.replace(/\b([A-Za-z]+)\./g, (tok, stem: string) => (ABBR.has(stem.toLowerCase()) ? stem + "\x00" : tok));
  o = o.replace(/\b[A-Z]\.\s/g, (m) => m.replace(/\./g, "\x00"));
  return o;
}
const restore = (s: string) => s.replace(/\x00/g, ".").replace(/\x01/g, "...");

function hardWordSplit(s: string, max: number): string[] {
  const out: string[] = []; let buf = "";
  for (const w of s.split(" ")) {
    if (buf && buf.length + 1 + w.length > max) { out.push(buf); buf = w; } else buf = `${buf} ${w}`.trim();
  }
  if (buf) out.push(buf); return out;
}
function splitOverrun(sentence: string, max: number): string[] {
  if (sentence.length <= max) return [sentence];
  let parts = sentence.split(CLAUSE).filter((p) => p && p.trim());
  if (parts.length <= 1) parts = sentence.split(/,\s+/).filter((p) => p.trim());
  if (parts.length <= 1) return hardWordSplit(sentence, max);
  const out: string[] = []; let buf = "";
  for (let part of parts) {
    part = part.trim();
    if (part.length > max) { if (buf) { out.push(buf); buf = ""; } out.push(...splitOverrun(part, max)); continue; }
    if (buf && buf.length + 1 + part.length > max) { out.push(buf); buf = part; } else buf = buf ? `${buf} ${part}`.trim() : part;
  }
  if (buf) out.push(buf);
  return out.filter((p) => p.trim());
}
export function splitSentences(text: string, maxChars = 400): string[] {
  if (!text || !text.trim()) return [];
  const raw = protect(text).split(SENT_END), out: string[] = [];
  for (let i = 0; i < raw.length; i += 2) {
    const s = restore(((raw[i] ?? "") + (raw[i + 1] ?? "")).trim());
    if (s) out.push(...splitOverrun(s, maxChars));
  }
  return out.map((s) => s.trim()).filter(Boolean);
}

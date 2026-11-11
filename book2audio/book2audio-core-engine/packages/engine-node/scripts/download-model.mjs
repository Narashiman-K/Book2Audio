// Downloads the Kokoro model once, into models/ (used offline afterwards, and bundled into the Windows installer).
// Usage: npm run download-model -w @b2a/engine-node -- [targetDir]
import { mkdir, writeFile, stat } from "node:fs/promises";
import { join, dirname } from "node:path";
const ID = "onnx-community/Kokoro-82M-v1.0-ONNX";
const root = process.argv[2] ?? join(process.cwd(), "models");
const base = join(root, ...ID.split("/"));
const api = async (p) => (await fetch(`https://huggingface.co/api/models/${ID}/tree/main/${p}`)).json();
const files = ["config.json", "tokenizer.json", "tokenizer_config.json", "onnx/model_quantized.onnx"];
const voices = (await api("voices")).map((f) => f.path).filter((p) => /voices\/(af|am|bf|bm)_/.test(p));
for (const f of [...files, ...voices]) {
  const dest = join(base, f);
  try { if ((await stat(dest)).size > 100) { console.log("have", f); continue; } } catch {}
  await mkdir(dirname(dest), { recursive: true });
  const r = await fetch(`https://huggingface.co/${ID}/resolve/main/${f}`);
  if (!r.ok) throw new Error(`${f}: ${r.status}`);
  await writeFile(dest, Buffer.from(await r.arrayBuffer()));
  console.log("got ", f);
}
console.log("Model ready in", base);

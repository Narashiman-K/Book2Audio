/** Minimal WAV helpers: 16-bit PCM mono. */
export function floatToWav(samples: Float32Array, sampleRate: number): Buffer {
  const n = samples.length, buf = Buffer.alloc(44 + n * 2);
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write("WAVE", 8); buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write("data", 36);
  buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) { const s = Math.max(-1, Math.min(1, samples[i]!)); buf.writeInt16LE(Math.round(s < 0 ? s * 0x8000 : s * 0x7fff), 44 + i * 2); }
  return buf;
}
export function concatSamples(parts: Float32Array[]): Float32Array {
  const out = new Float32Array(parts.reduce((a, p) => a + p.length, 0)); let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; } return out;
}
export const silence = (sec: number, sampleRate: number) => new Float32Array(Math.round(sec * sampleRate));

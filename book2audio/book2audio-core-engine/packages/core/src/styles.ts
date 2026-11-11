export interface Style {
  desc: string; baseRateDelta: number; basePitchDelta: number; emphasisRateDelta: number; emphasisPitchDelta: number;
  dialoguePitchDelta: number; pauseScale: number; maxRateDelta: number; maxPitchDelta: number;
}
export const STYLES: Record<string, Style> = {
  warm: { desc: "Gentle pitch lifts, soft pauses before big reveals, calm pace", baseRateDelta: 0, basePitchDelta: 0, emphasisRateDelta: -12, emphasisPitchDelta: 6, dialoguePitchDelta: 3, pauseScale: 1.0, maxRateDelta: 10, maxPitchDelta: 10 },
  motivational: { desc: "Energetic: stresses power words, punchy short pauses", baseRateDelta: 4, basePitchDelta: 2, emphasisRateDelta: -15, emphasisPitchDelta: 10, dialoguePitchDelta: 4, pauseScale: 0.7, maxRateDelta: 15, maxPitchDelta: 15 },
  storyteller: { desc: "Distinct pitch per dialogue line, dramatic scene pauses", baseRateDelta: -2, basePitchDelta: 0, emphasisRateDelta: -14, emphasisPitchDelta: 8, dialoguePitchDelta: 5, pauseScale: 1.25, maxRateDelta: 12, maxPitchDelta: 14 },
  minimal: { desc: "Only punctuation pauses, no word stress", baseRateDelta: 0, basePitchDelta: 0, emphasisRateDelta: 0, emphasisPitchDelta: 0, dialoguePitchDelta: 0, pauseScale: 1.0, maxRateDelta: 0, maxPitchDelta: 0 },
};
export const getStyle = (name: string): Style => STYLES[name] ?? STYLES.warm!;

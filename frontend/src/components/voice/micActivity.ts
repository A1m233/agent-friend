const MIC_ACTIVITY_THRESHOLD = 3;

export function micActivityIntensity(level: number, muted = false): 0 | 1 | 2 | 3 {
  if (muted || level <= MIC_ACTIVITY_THRESHOLD) return 0;
  if (level < 15) return 1;
  if (level < 40) return 2;
  return 3;
}

export function hasMicActivity(level: number, muted = false): boolean {
  return micActivityIntensity(level, muted) > 0;
}

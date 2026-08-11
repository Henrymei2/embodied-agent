export interface MotionPoint {
  x: number;
  y: number;
}

export const cancelledMotionPosition = (
  from: MotionPoint,
  to: MotionPoint,
  elapsedMs: number,
  durationMs: number,
  startDelayMs = 0,
): MotionPoint => {
  const movingElapsed = Math.max(0, elapsedMs - startDelayMs);
  const progress = durationMs > 0 ? Math.min(1, movingElapsed / durationMs) : 1;
  return {
    x: from.x + (to.x - from.x) * progress,
    y: from.y + (to.y - from.y) * progress,
  };
};

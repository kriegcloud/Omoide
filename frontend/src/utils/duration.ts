/**
 * m:ss clock for media durations. Round the TOTAL first: rounding only the
 * seconds part turned 59.6 s into "0:60" (review R1-01).
 */
export function formatClock(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

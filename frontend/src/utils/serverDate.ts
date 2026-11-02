/**
 * The API serializes persisted UTC timestamps without an offset
 * ("2026-11-02T18:14:30.976564"). `new Date()` reads such strings as LOCAL time,
 * which skewed ages by the UTC offset ("just now" for hours-old jobs, review
 * R6-01). Treat offsetless date-times as UTC; keep explicit offsets and
 * date-only strings unchanged.
 */
const OFFSETLESS_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseServerDate(value: string | number | Date): Date {
  if (value instanceof Date || typeof value === "number") return new Date(value);
  const trimmed = value.trim();
  return new Date(OFFSETLESS_DATETIME.test(trimmed) ? `${trimmed.replace(" ", "T")}Z` : trimmed);
}

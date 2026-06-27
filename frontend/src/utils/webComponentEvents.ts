function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function readCustomEventDetail<T>(event: unknown): T | undefined {
  if (!isRecord(event) || !("detail" in event)) return undefined;
  return event.detail as T | undefined;
}

export function readStringEventValue(event: unknown): string | null {
  const detail = readCustomEventDetail<unknown>(event);
  if (typeof detail === "string") return detail;
  if (isRecord(detail) && typeof detail.value === "string") return detail.value;

  const target = isRecord(event) ? event.target : undefined;
  if (isRecord(target) && typeof target.value === "string") return target.value;
  return null;
}

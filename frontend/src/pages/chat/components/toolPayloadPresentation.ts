export type ToolPayloadPresentation =
  | { kind: "json"; text: string; value: unknown }
  | { kind: "text"; text: string };

export interface FilesystemReadTextPresentation {
  content: string;
  metadata: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function presentToolPayload(raw: string): ToolPayloadPresentation {
  if (!raw) return { kind: "text", text: "" };
  try {
    const value: unknown = JSON.parse(raw);
    return {
      kind: "json",
      text: JSON.stringify(value, null, 2),
      value,
    };
  } catch {
    return { kind: "text", text: raw };
  }
}

export function presentFilesystemReadTextResult(
  raw: string,
): FilesystemReadTextPresentation | null {
  const payload = presentToolPayload(raw);
  if (payload.kind !== "json" || !isRecord(payload.value)) return null;
  if (typeof payload.value.content !== "string") return null;

  const metadata: string[] = [];
  const path = stringValue(payload.value.path);
  const encoding = stringValue(payload.value.encoding);
  if (path) metadata.push(path);
  if (encoding) metadata.push(encoding.toUpperCase());

  const startLine = numberValue(payload.value.start_line);
  const startColumn = numberValue(payload.value.start_column);
  const endLine = numberValue(payload.value.end_line);
  const totalLines = numberValue(payload.value.total_lines);
  if (startLine !== null && endLine !== null) {
    metadata.push(
      `第 ${startLine}–${endLine} 行${totalLines !== null ? ` / 共 ${totalLines} 行` : ""}`,
    );
    if (startColumn) metadata.push(`从第 ${startColumn} 列开始`);
  }

  if (payload.value.truncated === true) {
    const nextLine = numberValue(payload.value.next_start_line);
    const nextColumn = numberValue(payload.value.next_start_column);
    const next = nextLine === null
      ? ""
      : `；下一段从第 ${nextLine} 行${nextColumn ? `第 ${nextColumn} 列` : ""}开始`;
    metadata.push(`已截断${next}`);
  }

  return {
    content: payload.value.content,
    metadata: metadata.join(" · "),
  };
}

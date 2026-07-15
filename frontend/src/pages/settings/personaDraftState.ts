export interface PersonaDiffLine {
  kind: "unchanged" | "added" | "removed";
  text: string;
}

const MAX_DIFF_CELLS = 40_000;

/** 行级 LCS diff；人格正文过长时退化为整段替换，避免 UI 中进行超大矩阵计算。 */
export function buildPersonaDiff(before: string, after: string): PersonaDiffLine[] {
  if (before === after) {
    return before.split("\n").map((text) => ({ kind: "unchanged", text }));
  }
  const left = before.split("\n");
  const right = after.split("\n");
  if (left.length * right.length > MAX_DIFF_CELLS) {
    return [
      ...left.map((text) => ({ kind: "removed" as const, text })),
      ...right.map((text) => ({ kind: "added" as const, text })),
    ];
  }

  const lengths = Array.from(
    { length: left.length + 1 },
    () => new Uint32Array(right.length + 1),
  );
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      lengths[i][j] = left[i] === right[j]
        ? lengths[i + 1][j + 1] + 1
        : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }

  const result: PersonaDiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      result.push({ kind: "unchanged", text: left[i] });
      i += 1;
      j += 1;
    } else if (lengths[i + 1][j] >= lengths[i][j + 1]) {
      result.push({ kind: "removed", text: left[i] });
      i += 1;
    } else {
      result.push({ kind: "added", text: right[j] });
      j += 1;
    }
  }
  while (i < left.length) {
    result.push({ kind: "removed", text: left[i] });
    i += 1;
  }
  while (j < right.length) {
    result.push({ kind: "added", text: right[j] });
    j += 1;
  }
  return result;
}

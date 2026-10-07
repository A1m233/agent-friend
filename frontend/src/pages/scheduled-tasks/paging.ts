/** Refresh the whole visible window so inserts/deletes cannot leave stale page boundaries. */
export async function loadVisiblePages<T extends { id: string }>(
  fetchPage: (offset: number) => Promise<T[]>,
  pageCount: number,
  pageSize: number,
) {
  const rows = new Map<string, T>();
  let hasMore = false;
  for (let page = 0; page < pageCount; page++) {
    const items = await fetchPage(page * pageSize);
    for (const item of items) rows.set(item.id, item);
    hasMore = items.length === pageSize;
    if (!hasMore) break;
  }
  return { rows: [...rows.values()], hasMore };
}

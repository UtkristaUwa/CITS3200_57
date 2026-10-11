export const SORT_OPTIONS = [
  { value: 'relevance', label: 'Best Match / Relevance' },
  { value: 'closing_date_asc', label: 'Closing Date: Soonest first' },
  { value: 'closing_date_desc', label: 'Closing Date: Furthest away' },
  { value: 'publish_date_desc', label: 'Publish Date: Newest first' },
  { value: 'publish_date_asc', label: 'Publish Date: Oldest first' },
  { value: 'first_seen_at_desc', label: 'Discovered: Recently added' },
  { value: 'first_seen_at_asc', label: 'Discovered: Oldest first' },
  { value: 'updated_at_desc', label: 'Recently Updated' },
  { value: 'title_asc', label: 'Title: A to Z' },
  { value: 'title_desc', label: 'Title: Z to A' },
  { value: 'issuing_agency_asc', label: 'Agency: A to Z' },
  { value: 'issuing_agency_desc', label: 'Agency: Z to A' },
] as const;

export function parseSortOption(sortKey: string): { sort_by?: string; sort_dir?: 'asc' | 'desc' } {
  if (!sortKey || sortKey === 'relevance') {
    return { sort_by: 'relevance', sort_dir: 'desc' };
  }
  const lastUnderscore = sortKey.lastIndexOf('_');
  if (lastUnderscore !== -1) {
    const dir = sortKey.slice(lastUnderscore + 1);
    const field = sortKey.slice(0, lastUnderscore);
    if (dir === 'asc' || dir === 'desc') {
      return { sort_by: field, sort_dir: dir };
    }
  }
  return { sort_by: sortKey, sort_dir: 'desc' };
}

/**
 * Relevance cutoff, mirroring `min_visible_fit` in api/app/config.py.
 *
 * The API already filters low-fit tenders out for non-admins, so this is only
 * used to decide how an admin's view presents them: whether to hide them when
 * "show irrelevant" is off, and whether to tint them when it is on.
 */
export const MIN_VISIBLE_FIT = 40;

/** True for a tender a staff member would never be shown. An unscored tender
 *  (fit null) counts as low: the API hides those from staff too, since a
 *  failed relevance call leaves the score null. */
export function isLowFit(fit: number | null | undefined): boolean {
  return fit === null || fit === undefined || fit < MIN_VISIBLE_FIT;
}

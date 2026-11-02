/**
 * Shared trigger for infinite-scroll sentinels. The sentinel sits below the
 * tallest gallery column, so loading only when it is half visible let users
 * scroll through the empty space under shorter columns and the next batch then
 * filled in above the viewport. Start loading 1.5 screens ahead instead.
 */
export const INFINITE_SCROLL_MARGIN = "0px 0px 150% 0px";
export const infiniteScrollObserver = { rootMargin: INFINITE_SCROLL_MARGIN, threshold: 0 } as const;

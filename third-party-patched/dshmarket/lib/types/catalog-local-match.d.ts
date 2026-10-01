/**
 * Catalog matching for locally linked / file: installs. Shared by the host
 * restore route and the Market client so both refuse the same wrong guesses.
 */
export declare function findCatalogEntryForLocal<T extends {
    name: string;
    npm?: string | null;
    url: string;
}>(plugins: readonly T[], name: string, identities?: readonly string[], hints?: readonly string[]): T | null;
export type CatalogRestoreReason = 'no-catalog' | 'repo-mismatch';
/**
 * Why a local restore was blocked, when findCatalogEntryForLocal returned
 * null — and, when it matched, whether anything but the NAME agreed.
 *
 * `verified` is the difference between "put this back where it came from"
 * and "install the catalog's plugin that happens to share this name". A
 * local checkout with no declared repo — no git remote, no `repository`
 * field — gives the market nothing to match on, so a unique same-named
 * catalog entry is a guess. Usually a good one (#429: the local copy IS a
 * tweaked copy of that entry). Not always: @liuwenji007 reported a fork of
 * their own `dsh-humanizer` restored to a different author's plugin of the
 * same name (#485), which is someone else's code arriving under a button
 * labelled "restore".
 *
 * The match is kept, because refusing it would break the ordinary case, and
 * the caller is told not to present it as a certainty.
 */
export declare function resolveCatalogRestore<T extends {
    name: string;
    npm?: string | null;
    url: string;
}>(plugins: readonly T[], name: string, identities?: readonly string[], hints?: readonly string[]): {
    ok: true;
    entry: T;
    verified: boolean;
} | {
    ok: false;
    reason: CatalogRestoreReason;
};

/**
 * Discovery-time DSH compatibility metadata.
 *
 * The public catalog does not carry npm manifests. Fetching every manifest
 * while the market opens would turn one catalog request into more than a
 * thousand registry requests, so this module supplies a bounded, on-demand
 * index. Successful public manifest facts are cached beside the market's
 * profile state; conclusions are never cached because they depend on the DSH
 * version of the process serving the page.
 */
export type HostCompatibilityStatus = 'compatible' | 'incompatible' | 'unknown';
export type HostCompatibilityBasis = 'manifest' | 'undeclared' | 'unavailable';
export interface HostRequirementDeclaration {
    kind: 'engine' | 'peer';
    /** Present only for a peer-derived declaration. */
    package?: string;
    range: string;
}
export interface HostCompatibility {
    status: HostCompatibilityStatus;
    basis: HostCompatibilityBasis;
    /** Human-readable intersection of every raw declaration. */
    requirement: string | null;
    declarations: HostRequirementDeclaration[];
}
export interface NpmManifestFacts {
    version: string | null;
    enginesDsh: string | null;
    peerDependencies: Record<string, string>;
}
type FetchLike = (url: string, init?: {
    signal?: AbortSignal;
    headers?: Record<string, string>;
}) => Promise<Response>;
/** Keep only the small public subset of an npm manifest needed by discovery. */
export declare function manifestFacts(value: unknown): NpmManifestFacts;
/**
 * Derive the current host verdict from raw manifest facts.
 *
 * Every valid declaration is conjunctive: an explicit `engines.dsh` and all
 * host peers must agree. A malformed declaration keeps a passing result
 * unknown, but cannot erase a definite mismatch from another declaration.
 */
export declare function deriveHostCompatibility(facts: NpmManifestFacts | null, hostVersion: string | null, hostPackages: ReadonlySet<string>): HostCompatibility;
/**
 * Durable, bounded lookup of npm `latest` manifests.
 *
 * Failed requests are intentionally memory-only and short-lived: a mirror
 * outage must not become a day-long false "undeclared" result on disk.
 */
export declare class DiscoveryManifestIndex {
    private readonly cacheFile;
    private readonly entries;
    private readonly failures;
    private readonly inflight;
    private readonly fetcher;
    private readonly now;
    private readonly ttlMs;
    private readonly concurrency;
    private loaded;
    private consecutiveFailures;
    private unavailableUntil;
    private dirty;
    private activeFetches;
    private readonly fetchWaiters;
    constructor(cacheFile: string, options?: {
        fetcher?: FetchLike;
        now?: () => number;
        ttlMs?: number;
        concurrency?: number;
    });
    private load;
    private persist;
    /** One semaphore for the whole index, including overlapping HTTP batches. */
    private withFetchPermit;
    private fetchOne;
    /**
     * Facts for ONE named release, rather than for `latest`.
     *
     * The install and update routes can name the release they are about to
     * install — the compatibility dialog resolves one for this host (#581), and
     * the update route resolves the channel's target — and judging those
     * against `latest` is wrong in both directions: it refuses the compatible
     * older release the dialog just found for this host (because the newest
     * release declares a range this host misses), and it would equally pass a
     * pinned release that is itself incompatible.
     *
     * Deliberately outside the cache. The index is keyed by package name, and a
     * version-keyed one would grow with every release anyone ever pinned to
     * answer a question asked once per install. Nothing is recorded either: a
     * pre-flight verdict must not decide what the diagnostics panel sees next
     * (#619).
     *
     * A release whose manifest cannot be read is `null`, like every other
     * unreadable manifest: absence of a claim is not a verdict.
     */
    lookupVersion(name: string, version: string, registry: string): Promise<NpmManifestFacts | null>;
    /**
     * Look up a bounded batch while never exceeding the configured fan-out.
     *
     * `record: false` answers the caller from the cache or the network but
     * writes nothing back — not a success, not a failure, not the outage
     * counters. A pre-flight check uses this: it runs before an operation is
     * allowed to proceed, so whatever it learns must not decide what the
     * diagnostics panel sees next (#619).
     */
    lookup(names: readonly string[], registry: string, options?: {
        record?: boolean;
    }): Promise<Record<string, NpmManifestFacts | null>>;
}
/**
 * The newest release of `npmName` whose own declarations this host satisfies.
 *
 * The answer to "the newest version is too new for this host — what CAN I
 * install?" (#581), which the refusal dialog used to answer with nothing.
 *
 * Only a CONFIRMED `compatible` verdict from {@link deriveHostCompatibility}
 * passes. `unknown` — no declaration, or a host version nobody can read — is
 * skipped rather than offered: pinning a release as "compatible" on the
 * strength of a missing field would be the same guess this whole check exists
 * to avoid.
 *
 * Prereleases are included, and ordered properly (a release outranks its own
 * prereleases): in this ecosystem the host line is often a prerelease, and
 * plugins declare against it by name — skipping them would report "none
 * found" where the fix exists.
 *
 * `minimumVersionExclusive` restricts the search to releases NEWER than the
 * installed one, which is what an update needs: suggesting a downgrade is not
 * an update.
 *
 * @returns the version, or null when the packument cannot be read or nothing
 *   in the history declares itself compatible.
 */
export declare function findCompatibleVersion(npmName: string, hostVersion: string, hostPackages: ReadonlySet<string>, registry: string, fetcher?: FetchLike, minimumVersionExclusive?: string | null): Promise<string | null>;
export {};

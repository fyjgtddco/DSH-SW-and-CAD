/**
 * Restart-free installs: mount a freshly installed plugin into the running
 * composition through a market-owned Include subtree.
 *
 * Durable state stays with the profile's `dsh.profile.bundles` (reconciled by
 * the dsh CLI at install time), so the next boot loads the plugin through the
 * normal bundle layer. The subtree here exists only for the current process:
 * its input files live under `<profile>/.dsh-market/` and are wiped on every
 * boot, so a crash can never leave a file that collides with the bundle layer
 * (inserting an id the bundle layer also inserts is a hard boot failure).
 * `state.json` in the same directory is the market's own durable state
 * (disable list + custom groups) and deliberately survives the wipe.
 *
 * The Include subclass suppresses `write()` — the loader otherwise persists
 * tree changes back to the file it read (see dsh's agent-presets PresetTree
 * for the in-tree precedent).
 */
import { type Channel } from './channels.ts';
import { type Region } from './regions.ts';
interface HotRow {
    id: string;
    name: string;
}
interface PluginHandle {
    await(): Promise<unknown>;
    dispose(): Promise<unknown> | void;
}
interface HotContext {
    plugin(plugin: unknown, config: unknown): PluginHandle;
    logger?: {
        info?(message: string): void;
        warn(message: string): void;
    };
}
/**
 * Profile-scoped resolution for hot-mount rows: turn a bare package name into
 * the absolute `file://` entry URL of the package just installed into
 * `profileDir`.
 *
 * Include-tree rows reach `Include.import` as BARE names (`name:
 * '@scope/pkg'`), and the base class resolves them against the LOADER's own
 * location — the host closure
 * (`closures/<fp>/node_modules/…/cordis-plugin-loader`), whose parent walk
 * can never reach `home/profiles/<profile>/node_modules/`. Under a host whose
 * loader sits in an immutable dependency closure, EVERY market hot mount dies
 * with `Cannot find module '<pkg>' from '…/cordis-plugin-loader/…'` and falls
 * back to "restart required", blaming the plugin for what is a resolution
 * anchor problem.
 *
 * Resolving the row name HERE, against the profile the package was actually
 * installed into, is anchor-independent: `require.resolve` walks
 * `profileDir/node_modules` natively, so the tree hands the loader a
 * `file://` URL needing no further resolution. Non-bare specifiers (relative
 * paths, `file://`, `cordis:` builtins) and names that do not resolve under
 * the profile pass through unchanged, preserving base-class semantics for
 * every shape this fix does not own.
 *
 * The fallback keeps the name bare rather than synthesising a URL: a package
 * whose entry cannot be located via `require.resolve` (no `main`/exports —
 * the market's own `entryArtifactExists` heuristic covers those shapes before
 * an install is accepted) is not something this resolver should guess about.
 * Client-only shims never reach this function (their rows are replaced by a
 * no-op host module before the file is written).
 */
export declare function resolveProfileEntry(profileDir: string, name: string): string;
/**
 * Insert rows of a plugin's bundle patch, or null when the patch contains
 * anything beyond plain `id`/`name` insert rows (config blocks, disables,
 * expressions) — those compositions fall back to restart activation.
 */
export declare function parseSimplePatch(patchText: string): HotRow[] | null;
/**
 * Wipe leftover hot-mount inputs; call once when the market host starts.
 * `state.json` (disable choices + groups) deliberately survives.
 */
export declare function cleanHotDir(profileDir: string): void;
/** Persisted market state: the generic disable list plus custom groups. */
export interface MarketState {
    /** Plugins the user switched off; replayed at every boot. */
    disabled: Set<string>;
    /** User-defined plugin groups: group name → member package names. */
    groups: Record<string, string[]>;
    /** Display order of group names; "ungrouped" is implicit and never listed. */
    groupOrder: string[];
    /**
     * The user's own one-line note per installed plugin (#347).
     *
     * A catalog description answers "what is this", written by its author for
     * strangers and often in a language the reader did not pick. It cannot
     * answer "why did I install this" — which is the question someone with
     * forty plugins is actually asking. So a note REPLACES the description on
     * that row, and the original stays one click away.
     *
     * Local state like the disable list and the groups beside it: never sent
     * anywhere, and carried by a backup because it is part of how this profile
     * is set up.
     *
     * Optional on the way IN: several callers build a state object from the
     * few fields they own and hand it to writeMarketState. Requiring this one
     * would make every such call a silent way to erase every note — the exact
     * shape of #339, where a partial snapshot dropped a field nobody was
     * thinking about. Omitting it means "leave them alone" instead.
     */
    notes?: Record<string, string>;
    /**
     * The release channel the user PICKED, absent until they pick one.
     *
     * Absent is not the same as 'stable': with no choice on record the channel
     * is derived from the running build, so installing a prerelease by hand
     * puts you on the beta channel without a second step. Once chosen, the
     * choice is the answer — including "stable" while a beta is running, which
     * is how someone gets back off the channel.
     */
    channel?: Channel;
    /**
     * The download region in force, absent until something has decided one.
     *
     * Absent means "nobody has decided yet", which is what triggers the
     * one-time network probe. Once a value is here — whether the probe wrote
     * it or the user picked it — no further probing happens, so the market
     * does not silently change routes between runs.
     */
    region?: Region;
    /**
     * Whether `region` was chosen by the probe rather than by the user.
     *
     * Only drives a one-time notice explaining why the market picked what it
     * picked. A user who never learns a route was chosen for them has no way
     * to know the setting exists, and no reason to look for it when something
     * downloads oddly.
     */
    regionAuto?: boolean;
    /**
     * Catalog entry URLs the user bookmarked for later install (#414).
     * Keys are registry `url` strings, not package names — favorites are a
     * pre-install list, unlike groups/notes which target installed packages.
     */
    favorites?: string[];
    /**
     * Package names the user hid from Discover and Themes (#657).
     *
     * Local, per-profile, reversible. The Hidden tab lists the same names so
     * the choice can be undone. Independent of `disabled` (the plugin can keep
     * running) and of the session-only update ignore (that one dies on restart).
     * A stored name may be the installed package name or the catalog package
     * name; the client treats both as one plugin when it can match them.
     *
     * Optional on the way in, like `notes` / `favorites`: several callers
     * build a state object from the few fields they own, and requiring this
     * one would make every such call a silent way to erase the list (#339).
     */
    blocked?: string[];
    /**
     * Installed package names the user asked not to be reminded about (#728).
     *
     * This is not {@link blocked}. Hiding a plugin takes it off Discover.
     * This list leaves the plugin where it is, and leaves the update itself
     * on the installed row. It only stops the reminder: the badge, the
     * "update all" count, the sort-to-top, and the reminder bar. A new
     * version is still listed, and the row's own Update button still works.
     *
     * Optional on the way in, for the same reason as `blocked` (#339).
     * An empty array is how the last name is removed.
     */
    updateExempt?: string[];
    /** User-supplied HTTPS prefix used when the built-in GitHub routes fail. */
    githubProxy?: string;
    /**
     * Packages the market removed from the profile's own declarations because
     * the build on disk can no longer compose (#663).
     *
     * The case this exists for: an update blocked by open files leaves the
     * target directory incomplete, `keepLockedBuild` cannot keep the previous
     * build, and the profile goes on declaring a package whose `package.json`
     * is missing. The next start dies in composition — on Desktop the window
     * never opens — and the only way out was uninstalling the plugin by hand.
     * So the declaration is dropped instead (a rename or a delete of the
     * directory itself cannot be attempted: the same lock that stopped pnpm
     * refuses the market's move too), and this map is what keeps that from
     * being silent. The plugin disappears from the installed list; this is the
     * record saying why, and what spec to reinstall.
     *
     * Optional on the way in, like `notes`: several callers write a state
     * object built from the few fields they own, and requiring this one would
     * make every such call a way to erase every entry (#339's shape).
     */
    brokenPlugins?: Record<string, BrokenPlugin>;
    /**
     * The operator's pinned build environment (issue #336) as saved from the
     * market's own settings card. Absent means "the composition says" — the
     * entry's `config.buildEnv`, or nothing at all. Present (non-empty) means
     * a saved editor state REPLACES the composition map, so clearing the
     * field on the card inherits the composition again rather than freezing
     * an earlier save. Read live into `config.buildEnv` by the routes; see
     * src/dsh-cli.ts spawnEnv for the precedence once it reaches a child.
     */
    buildEnv?: Record<string, string>;
}
/** Why a package is no longer declared, and what to put back. */
export interface BrokenPlugin {
    /** The dependency spec it had before removal — the reinstall target. */
    spec: string;
    /** Machine-readable cause; the UI owns the wording. */
    reason: 'incomplete-build-locked';
    /** When the market removed the declaration (ISO 8601). */
    at: string;
}
/** Upper bound on bookmarked catalog URLs kept in state.json (#414). */
export declare const MAX_FAVORITES = 500;
/** Upper bound on blocked package names kept in state.json (#657). */
export declare const MAX_BLOCKED = 500;
/** Upper bound on the persistent do-not-remind list (#728). */
export declare const MAX_UPDATE_EXEMPT = 500;
/** npm package names are at most 214 characters. Longer values are not names. */
export declare const MAX_BLOCKED_NAME = 214;
/**
 * Sanitize an untrusted build-env map (state.json, or the card route's body)
 * into the shape spawnEnv can merge.
 *
 * An empty map and a non-object both read as undefined: clearing the card
 * must inherit the composition, and a blank line in state.json must not
 * disable every pinned variable. Only the merge precedence in
 * src/dsh-cli.ts spawnEnv — never this — protects PATH and CI, but a value
 * a user typed for them would silently do nothing there, so it is rejected
 * here with a reason instead.
 *
 * `GIT_ASKPASS` and `SSH_ASKPASS` are deliberately NOT rejected, though they
 * are the two names that can re-open a credential prompt: pointing them at a
 * program is the supported non-interactive way to answer one, and #587/#596
 * close the *terminal* fallback (GIT_TERMINAL_PROMPT, BatchMode) rather than
 * the program one. A user who pins these has already said where the answer
 * comes from; a user who does not still gets the closed prompt.
 */
export declare function buildEnvFromUnknown(value: unknown): Record<string, string> | undefined;
/**
 * Read the whole market state. Legacy `disabledSkins` (the pre-#60
 * theme-only key) still loads; every new write uses the generic `disabled`
 * key (#60).
 */
/** A note is a label, not a document: one line, bounded so state.json cannot
 * grow without limit from a paste. */
export declare const MAX_NOTE = 200;
export declare function readMarketState(profileDir: string): MarketState;
/**
 * Persist the whole market state.
 *
 * Every field a caller does not carry forward is taken from disk rather than
 * dropped. Several callers legitimately know about only one part of the
 * state — `writeMarketState(dir, { disabled, groups, groupOrder })` appears
 * at five call sites in routes.ts — and before #435 that shape silently
 * erased whatever else the user had chosen:
 *
 * - `channel` and `region` had no fallback at all, so toggling any plugin
 *   threw away the user's update channel and download region. Both are
 *   deliberate choices made through the settings card, and neither has a
 *   "clear it" path: once picked they only ever move to another value. So
 *   an absent one always means "the caller has nothing to say", never
 *   "the user unchose it".
 * `notes` keeps its original rule — an explicit object wins, including an
 * empty one, because deleting the last note has to be expressible. What made
 * #435 lose notes was not this function but a caller: the note route wrote
 * through a fresh read while the long-lived `marketState` in routes.ts still
 * carried `notes: {}` from boot, and the next write from that object put the
 * empty one back. The fix for that belongs at the call site, where the two
 * copies are, not here — see the note route.
 *
 * Reading before writing costs one small JSON parse on an operation that is
 * already doing filesystem work, and it is what makes "this function writes
 * the whole document" safe for callers that only hold part of it.
 */
export declare function writeMarketState(profileDir: string, state: MarketState): void;
/** Plugins the user switched off; skipped by the boot re-mount. */
export declare function readDisabled(profileDir: string): Set<string>;
/** Persist just the disable list, preserving groups and order. */
export declare function writeDisabled(profileDir: string, disabled: Set<string>): void;
/** @deprecated theme-specific alias — kept for pre-#60 callers. */
export declare function readDisabledThemes(profileDir: string): Set<string>;
/** @deprecated theme-specific alias — kept for pre-#60 callers. */
export declare function writeDisabledThemes(profileDir: string, disabled: Set<string>): void;
/** Package names currently live through a market hot mount (patch or shim). */
export declare function listHotMounts(): string[];
/** Outcome of one hot-mount attempt; `reason` explains non-`ok` results. */
export interface HotMountResult {
    ok: boolean;
    /** Bilingual reason shown to the user instead of a bare restart banner. */
    reason: string | null;
}
/**
 * Dispose a plugin hot-mounted earlier in this session, removing it from the
 * running composition immediately.
 * @param packageName - package to unmount.
 * @returns true when a live hot mount was found and disposed.
 */
export declare function hotUnmount(packageName: string): Promise<boolean>;
/**
 * Mount `packageName` (just installed into the profile) into the running
 * composition.
 * @param ctx - market host context; the subtree unwinds with the market's fiber.
 * @param profileDir - profile the package was installed into.
 * @param packageName - installed package to activate.
 * @returns whether the plugin is live without a restart, plus the reason
 * when it is not (P0-2: the UI must distinguish "restart will fix it" from
 * "this package can never hot-mount").
 */
export declare function hotMount(ctx: HotContext, profileDir: string, packageName: string): Promise<HotMountResult>;
/**
 * Mount every installed client-only package (`dsh.client` without
 * `dsh.bundle`) at market startup. The bundle reconcile skips these packages
 * entirely, so without the market's shim their client bundles are unreachable
 * in every boot — this is what makes them behave like normal plugins.
 * @returns names that were mounted.
 */
export declare function mountClientOnlyDeps(ctx: HotContext, profileDir: string): Promise<string[]>;
/**
 * Row ids and package names the user's own patch layer (cordis.patch.yml)
 * already contains. Line-wise scan on purpose: the file may hold structures
 * the market's strict patch parser rejects, but any mention of a row id or
 * package name is enough to know the user manages it (#58).
 */
export declare function readUserPatchControls(profileDir: string): {
    ids: Set<string>;
    names: Set<string>;
};
/**
 * Whether the user patch layer manages `name` — matched by exact package
 * name or by the plugin-manager row-id convention (strip the leading @,
 * non-alphanumerics to '-', lowercase).
 */
export declare function patchLayerManages(controls: {
    ids: Set<string>;
    names: Set<string>;
}, name: string): boolean;
/**
 * Delete the market's own state directory.
 *
 * `cleanHotDir` wipes the ephemeral hot-mount inputs on every boot but
 * deliberately preserves `state.json` — the disable list and custom groups
 * are the user's durable choices. Uninstalling the market is the one moment
 * where removing them is the right thing, and only when the user asked.
 * @returns true when a directory was there to remove.
 */
export declare function purgeMarketState(profileDir: string): boolean;
export {};

/** Locate the DSH host package in CLI and packaged Desktop runtimes. */
/**
 * Directories that can hold the DSH installation's OWN bundle layer — the
 * Desktop shell's package root and its `cordis.patch.yml`.
 *
 * The shell applies that file itself rather than declaring it through
 * `dsh.profile.bundles`: dsh-plugin-desktop's launcher loads it by hand and
 * splices it in directly behind the `@deepseek-ai/dsh-web-app` layer
 * (`src/profile.ts`, `DESKTOP_PATCH_PATH`), which is where the rows
 * `desktop-shell`, `desktop-notifications`, `desktop-terminal` and the rest
 * come from at all.
 *
 * Its package root is NOT where {@link findDshInstallDir} points. On the
 * official Desktop the market's `desktopProfiles` branch hands the analysis no
 * install dir at all, and the directory the lookup does answer with is
 * `node_modules/@deepseek-ai/dsh` — whose own manifest declares no bundle
 * patch. So the application root is offered from the two directions the host
 * lookup already knows, and the caller reads each candidate's own manifest:
 *
 *  - the located install package's ancestors:
 *    `<app>/node_modules/@deepseek-ai/dsh` walks up to `<app>`;
 *  - Electron's resources directory under each application-root name — which
 *    is the same directory {@link findDshInstallDir} probes for the in-box
 *    bundles, one level up.
 *
 * This is the layering #405 fixed one level down: the in-box bundles are read
 * from that application root, and the shell's own overlay file lives beside
 * them.
 *
 * @param dshInstallDir - install directory to walk up from, when one is known.
 * @returns candidate directories, most specific first, duplicates removed.
 */
export declare function desktopApplicationRoots(dshInstallDir: string | null): string[];
/**
 * The version of the DSH host this market is running inside.
 *
 * CLI versions come from the host manifest. Flat Desktop shells additionally
 * require agreement with their bundled split runtime, never profile packages.
 *
 * Worth reporting because the host version has repeatedly been the thing
 * neither side could see. #293 turned on it (the reporter was on
 * 0.1.1-rc.2 while every attempt to reproduce had been on 0.1.0-rc.8, which
 * nobody knew until three rounds in), and #404 is entirely about a plugin
 * that requires a host newer than the Desktop build it was installed on.
 *
 * The directory comes back too, because WHERE it was found is the other half
 * of the answer: a path under Electron's resources is a Desktop-bundled host,
 * which #139 established can be older than whatever `npm ls` would report.
 * Asking the user is not a substitute — that is the number they do not have.
 * @returns the host version and the directory it was read from, or null when
 * no host package is locatable (a plain `dsh web` from a global install can
 * legitimately land here).
 */
export declare function dshHostInfo(entry?: string): {
    version: string;
    directory: string;
} | null;
/**
 * Walk up from the CLI entry first, then inspect Electron's authoritative
 * resources directory. Desktop distributions may keep node_modules outside
 * the ASAR, expose them through ASAR's virtual filesystem, or disable ASAR.
 *
 * The entry is resolved through symlinks before the walk, because for a
 * globally installed dsh it IS one. `npm i -g` and Homebrew both put a link
 * in a `bin/` directory pointing at the real package, so `process.argv[1]`
 * is `/opt/homebrew/bin/dsh` and walking up from there reaches `/` without
 * ever passing the package. Measured: the same call answers `null` for the
 * link and the correct directory for its target.
 *
 * That was not a cosmetic gap. Everything downstream reads the host version
 * from here — the exported log's `dsh host:` line (#426), Discover's
 * host-requirement column and filter (#473), and the pre-update check
 * (#404) — and a null version makes every one of them answer "unknown",
 * silently, on exactly the ordinary global install. A bundled Desktop host
 * is reached by the resources branch below and was never affected, which is
 * why this survived: the case that worked is the one that gets tested.
 */
export declare function findDshInstallDir(entry?: string): string | null;

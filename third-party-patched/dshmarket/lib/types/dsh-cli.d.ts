/**
 * Process layer: re-invoking the dsh CLI that launched this host, spawning
 * `dsh plugin` commands with timeouts and live progress, and provisioning
 * pnpm. This is the only module that starts child processes.
 *
 * Installs run through node:child_process, not ctx.shell: the shell service is
 * the agent's sandboxed executor and denies writes to the profile directory.
 */
import type { ChildProcess } from 'node:child_process';
import { type ProgressPhase } from './ndjson.ts';
import { type Region } from './regions.ts';
/**
 * The real Node executable for spawning children. On Android the kernel runs
 * node through the dynamic linker, so `process.execPath` is
 * `/apex/.../linker64` — spawning IT with `--expose-internals` makes the
 * linker treat the flag as the program path and die with
 * `error: expected absolute path: "--expose-internals"`. `process.argv0`
 * carries the real node binary; prefer it whenever it is an existing
 * absolute path, and fall back to execPath everywhere else.
 * @param argv0 - `process.argv0`, injectable for tests.
 * @param execPath - `process.execPath`, injectable for tests.
 */
export declare function nodeExecutable(argv0?: string | undefined, execPath?: string): string;
/**
 * The directory holding the Node binary running this process. `npm`,
 * `npm.cmd` and `corepack` are installed alongside it by every official Node
 * distribution, so it is the one place the toolchain can be looked for
 * without guessing — and unlike a PATH entry it cannot be absent, because
 * this process is executing out of it.
 *
 * #167: a Windows desktop host spawned dsh without the Node install
 * directory on PATH. Node itself was running (v24.18.1 in the log) while
 * both `corepack` and `npm` came back "not recognized as an internal or
 * external command", so the one-click setup had no way to succeed.
 */
export declare const nodeBinDir: string;
/**
 * Translate the machine's proxy environment into the ONE form pnpm reads.
 *
 * `HTTPS_PROXY` / `http_proxy` are what every other tool honours, and what
 * `net.ts` already routes the market's own catalog fetches through — but
 * pnpm ignores them completely. It reads npm config, so a proxy reaches it
 * only as `npm_config_https_proxy` / `npm_config_proxy` (or an .npmrc entry,
 * which is the user's file and not ours to rewrite).
 *
 * That gap is why the market could load its catalog through a proxy and
 * then hang installing anything at all — reported four separate times
 * (#148, #161, #188, #232), always from a network that needs one.
 *
 * An `npm_config_*` value the caller already set always wins: it is the more
 * specific statement of intent, and on Windows env keys are case-insensitive
 * so the check has to be too. NO_PROXY is forwarded verbatim because pnpm
 * reads `npm_config_noproxy` and a host excluding its own registry mirror
 * must keep excluding it.
 */
export declare function proxyEnvForPnpm(env?: NodeJS.ProcessEnv, region?: Region): NodeJS.ProcessEnv;
/**
 * Directories to append to PATH so a spawned pnpm can be found (#32, #38,
 * #167, #292).
 *
 * A GUI or desktop launch inherits none of the shell profile, so PATH holds
 * whatever the launcher had — usually not the directory the user's package
 * manager lives in. The market appends the places it is actually installed
 * to rather than telling the user to fix their environment.
 *
 * Windows used to get only the Node directory, which made the market's own
 * advice unfollowable: the error it prints recommends installing pnpm with
 * `iwr https://get.pnpm.io/install.ps1`, and then it did not look where that
 * installer puts it (#292). Both Windows layouts are covered now — the
 * standalone installer's `%LOCALAPPDATA%\pnpm`, and `%APPDATA%\npm` where
 * `npm i -g pnpm` writes `pnpm.cmd`.
 *
 * `PNPM_HOME` comes first on every platform: the installer sets it, so it is
 * the one answer that is right even when the layout is not the default one.
 *
 * @param platform - `process.platform`, injectable for tests.
 * @param env - environment, for PNPM_HOME and the Windows app-data roots.
 * @param home - home directory, injectable for tests.
 */
export declare function toolSearchDirs(platform?: string, env?: NodeJS.ProcessEnv, home?: string): string[];
/**
 * Stop git asking for credentials down a channel nobody is listening on
 * (#587).
 *
 * `CI=true` below is the same defence one layer up, and pnpm reads it. git
 * does not — it has its own switch, and it was not set. The gap only opens
 * when a spec reaches pnpm's git fetcher instead of the codeload tarball
 * path `accelerate.ts` describes: `github:owner/repo#path:/sub` is one, and
 * pnpm really does shell out to `git` for it, trying HTTPS first.
 *
 * git's credential prompt opens the controlling terminal, not stdin. There
 * is no terminal here, so the question is never seen and never answered:
 * the reporter caught `git.exe` alive for eight minutes having burned 0.05s
 * of CPU, and only the fifteen-minute install timeout ended it. Refusing
 * the prompt turns that into a fast, readable failure.
 *
 * It is a default, not an override: a value the caller set wins, and blank
 * counts as unset because an empty `GIT_TERMINAL_PROMPT` is not a setting
 * git can parse either. Credential helpers and `GIT_ASKPASS` are untouched
 * and still answer first — this closes only the terminal fallback, which is
 * precisely the branch that cannot work from a spawned child.
 *
 * Scope, stated plainly because it is narrower than the issue title
 * suggests: this is the HTTPS half. pnpm falls back to `git@github.com:`
 * when HTTPS fails, and the ssh side needs `GIT_SSH_COMMAND`, which
 * overrides `core.sshCommand` and `GIT_SSH` — the two ordinary ways to
 * choose an identity — and whose `BatchMode=yes` would disable
 * `SSH_ASKPASS`, breaking key-passphrase installs that work today. That
 * half needs a policy decision, so it is not made here. Note also that on
 * POSIX `runDshPlugin` spawns detached, so the subtree has no controlling
 * terminal and the prompt already dies instantly; the hang the issue
 * reports needs Windows, where the spawn is not detached.
 */
export declare function gitEnvForPnpm(env?: NodeJS.ProcessEnv): NodeJS.ProcessEnv;
/**
 * Every `--config.<key>=<value>` override in the argv, repeated as the
 * `PNPM_CONFIG_<KEY>` environment variable that pnpm 12 still reads.
 *
 * pnpm 12 ignores some `--config.<key>` overrides on the command line, in
 * either spelling, without a word: `fetchTimeout` (#615; measured on
 * 12.2.1, 12.3.0 and 12.4.1) and `auto-install-peers` (12.4.1 auto-installs
 * the peer with the flag present and not with the variable), so the retries
 * that carry them ran exactly like the first attempt. `PNPM_CONFIG_*` is
 * honoured by 11.8, 11.21 and 12.4 alike, so the argument stays for the
 * versions that read it and the variable carries the same value for the
 * ones that do not. Scoped to the run that carries the flag; nothing is set
 * otherwise, so a user's own values are untouched on every other run. Keys
 * are accepted in either spelling, so respelling a constant (as #600 did
 * for the release-age one) cannot silently drop the variable.
 */
export declare function pnpmConfigEnvForArgs(pluginArgs: readonly string[]): NodeJS.ProcessEnv;
/**
 * Windows npm/corepack/pnpm are `.cmd` shims. Node's `spawn` without a shell
 * cannot start them (ENOENT / EINVAL). Same pattern as dsh's `plugin` forwarder.
 */
export declare const winCmdShim: boolean;
/**
 * Quote one argv token for a cmd.exe `/c` command line. cmd only groups with
 * double quotes, so a token that needs quoting gets wrapped and embedded
 * quotes are doubled.
 */
export declare function quoteCmdArg(arg: string): string;
/**
 * Build a cmd.exe command line from argv. Only the Windows shim path uses
 * this: cmd re-parses the joined string, so every token is quoted before
 * joining.
 */
export declare function cmdCommandLine(argv: readonly string[]): string;
/**
 * Whether a profile name can cross the rare Windows `dsh.cmd` fallback.
 *
 * cmd.exe expands percent-delimited environment variables even inside a
 * quoted argument. Keep that fallback to names made only of letters, marks,
 * numbers, spaces, dots, underscores, and hyphens. The normal direct-Node
 * launcher remains argv-safe and accepts every DSH-valid profile name.
 */
export declare function isCmdSafeProfileName(profile: string): boolean;
/**
 * Argv re-invoking the CLI that launched this host process, so installs work
 * whether dsh runs from a global bin, a local install, or repo source
 * (`node --import tsx/esm .../bin.ts`). Falls back to a PATH `dsh`.
 */
export declare function dshArgv(): {
    file: string;
    args: string[];
    cwd: string | undefined;
    viaShell: boolean;
};
/** Outcome of one spawned plugin command. */
export interface InstallResult {
    exitCode: number | null;
    timedOut: boolean;
    stdout: string;
    stderr: string;
    /** True when the run ended because the user cancelled it. */
    cancelled: boolean;
    /** Desktop's generation-wide package-operation gate rejected the start. */
    busy?: boolean;
    /** Package names pnpm reported as having ignored build scripts (ndjson). */
    ignoredBuilds?: string[];
    /**
     * pnpm's OWN error message and code, from its structured ndjson stream
     * (#244).
     *
     * Without this the only thing a failure could report was the tail of
     * stderr — which for a market install is dsh's wrapper line, "pnpm failed
     * in profile directory …", identical for every possible cause. pnpm's
     * real error never went to stderr at all; it goes to the ndjson stdout
     * this already parses for progress, and was being thrown away on the way
     * out. Three separate reports (#244, #192, #138) are all "the UI shows a
     * stack tail and nothing else".
     */
    pnpmError?: string;
    pnpmErrorCode?: string;
    /**
     * Exact npm version this Desktop run actually handed to the host (#496).
     *
     * Anywhere Labs' install boundary rewrites a floating dist-tag (or bare
     * name) to `name@x.y.z` with its own registry fetch. The update route
     * normally pins that version itself before `add`, so this field matches
     * the request and is unused for verification. It matters when registry
     * metadata was unavailable and the add still carried `@latest`/`@beta`:
     * verification then adopts this pin instead of leaving the expected
     * version unset. When the route already sent an exact pin, callers must
     * keep that pin authoritative and must not let this field lower it.
     */
    resolvedNpmVersion?: string;
}
/** The shape every orchestration function takes to run plugin commands (injectable in tests). */
export type PluginRunner = (profile: string, pluginArgs: string[]) => Promise<InstallResult>;
/** Package-operation boundary consumed by the HTTP route layer. */
export interface PluginCommandRuntime {
    runPlugin: PluginRunner;
    probePnpm(): Promise<boolean>;
    provisionPnpm(): Promise<{
        ok: boolean;
        hint?: string;
    }>;
    cancelActive(): boolean;
    /** Whether this host can execute an immutable rollback add target. */
    supportsExactRollbackTarget?(target: string): boolean;
}
/** One running package operation, however it was started. */
export interface DesktopPnpmHandleLike {
    readonly stdout: NodeJS.ReadableStream;
    readonly stderr: NodeJS.ReadableStream;
    readonly done: Promise<{
        readonly exitCode: number | null;
        readonly signal: NodeJS.Signals | null;
    }>;
    cancel(): void;
}
/**
 * Structural subset of DSH Desktop's public `desktopPnpm` contract.
 *
 * Anywhere Labs' DSH Desktop is ONE third-party client among several, and
 * this interface exists only for it. Nothing here is part of the official
 * DSH protocol — `desktopPnpm`, `installPlugin` and the install boundary
 * below appear nowhere in `@deepseek-ai/*`. Every other client the market
 * runs under, including other desktop apps, installs through the ordinary
 * `dsh plugin --profile <p> add` CLI, and so does the market itself when
 * none of these services are present.
 *
 * That is why every member past `runPlugin` is optional and reached by
 * feature detection. A host that does not publish one simply never enters
 * the branch, and the ordinary path it already used stays untouched — the
 * cost of accommodating one vendor must not be paid by the others, or by
 * the far larger number of people on plain `dsh web`.
 */
export interface DesktopPnpmLike {
    runPlugin(args: readonly string[], invokingDir: string, signal?: AbortSignal): DesktopPnpmHandleLike;
    /**
     * Desktop 2.x refuses `add` through `runPlugin` — "plugin add must use the
     * recoverable install boundary" (#215, #219, #272) — and offers this
     * instead, which their launcher enables only for the selected market
     * provider. Same arguments, same handle, no recovery receipt and no
     * write-ahead log for the caller to reconcile.
     *
     * Optional because it is theirs: absent on every other host, including
     * the other third-party desktop client in #292, which installs perfectly
     * well through the ordinary CLI.
     *
     * Read from their published source rather than assumed: it accepts ONLY
     * `add` with exactly one target of the form `name@exact.version`
     * (`validateExternalMarketInstallArgs` in dsh-plugin-desktop/src/pnpm.ts).
     * A `github:owner/repo` target is rejected before any process starts, so
     * the 1085 catalog entries with no npm package — 57% of it — cannot be
     * installed on that host by any spelling this market could send. That is
     * a gap in their contract, not something to work around here.
     */
    runExternalMarketPluginInstall?(args: readonly string[], invokingDir: string, signal?: AbortSignal): DesktopPnpmHandleLike;
}
/** Desktop runtime also owns cleanup of any operation started by this fiber. */
export interface DesktopPluginRuntime extends PluginCommandRuntime {
    dispose(): Promise<void>;
}
/**
 * Kill a spawned child and, on Windows, its whole process tree — `kill()`
 * there only terminates the wrapper, leaving pnpm children running.
 * (Contributed in #7 by @mraing.)
 */
export declare function killChild(child: ChildProcess): void;
/**
 * Cancel the plugin command currently running.
 * @returns true when there was one to cancel.
 */
export declare function cancelActive(): boolean;
/**
 * The package manager the host itself supplies, as the launcher hands it
 * over (`profileContext.packageManager`).
 *
 * This is not a PATH executable. A packaged host ships its own runtime and
 * describes it as a whole invocation — command, args AND env — because each
 * part carries meaning: the desktop host passes the location of its embedded
 * Node through `env`, so an implementation that keeps only `command` reaches
 * a tool it still cannot execute, which is the reported failure (#653).
 */
export interface HostPackageManager {
    readonly command: string;
    readonly args: readonly string[];
    readonly env: NodeJS.ProcessEnv;
}
/**
 * Register the host's package manager for this process, or clear it with
 * `null`. Cached probe answers are dropped only when the invocation really
 * changes, so a repeated mount does not re-probe a toolchain that works.
 */
export declare function setHostPackageManager(invocation: HostPackageManager | null): void;
/** The host-supplied package manager, for callers that must name it. */
export declare function hostPackageManagerInvocation(): HostPackageManager | null;
/** Why the host-supplied package manager last failed, or null. */
export declare function lastHostPackageManagerFailure(): {
    command: string;
    output: string;
} | null;
/** Why `pnpm --version` last failed, or null when it has not failed. */
export declare function lastPnpmProbeFailure(): {
    kind: 'missing' | 'failed';
    output: string;
} | null;
/**
 * Whether a package manager is usable — the host-supplied one first.
 *
 * The tiers are ordered by what the machine can actually run: a packaged
 * host's bundled runtime exists whether or not PATH was inherited from a
 * shell, and on the reported machine only that tier can work at all (#653).
 * A host tier that fails falls through to the PATH probe, so a host that
 * publishes a broken invocation cannot make things worse than they were.
 */
export declare function probePnpm(): Promise<boolean>;
/**
 * Provision pnpm without user involvement: corepack (ships with Node) first,
 * a global npm install as fallback.
 * @returns true when `pnpm --version` succeeds afterwards.
 */
export declare function provisionPnpm(): Promise<{
    ok: boolean;
    hint?: string;
}>;
/**
 * Whether a bare command name resolves to a file on the PATH the market
 * hands its children.
 *
 * The market cannot read the reason a spawn failed out of the child's
 * message: cmd.exe reports a missing command in the console's ANSI codepage
 * ("'npm' 不是内部或外部命令" on a Chinese Windows), which is neither the
 * string `ENOENT` nor even valid UTF-8 — so the #32 hint, written against
 * Node's own ENOENT wording, could never fire on Windows and the user was
 * left with no guidance at all (#167). Looking on disk answers the same
 * question in every locale.
 */
export declare function toolOnPath(name: string): boolean;
/**
 * Why the one-click pnpm setup failed, in terms the user can act on.
 *
 * Every one of these was a real report where the market said only "自动准备
 * 没成功" while the log held the actual cause: EEXIST (#142 — corepack had
 * already placed a pnpm shim, so `npm -g` refused to overwrite it), EPERM
 * (#108 — Node installed somewhere the user cannot write), ENOENT (#32 —
 * a GUI launch with no Node on PATH at all).
 * @returns a bilingual, actionable hint, or undefined when unrecognized.
 */
export declare function provisionHint(corepackOutput: string, npmOutput: string, npmFound?: boolean, probeFailure?: {
    kind: 'missing' | 'failed';
    output: string;
} | null): string | undefined;
/** Live progress of the running plugin command, for the status route. */
export interface InstallProgress {
    active: boolean;
    target: string;
    startedAt: number;
    lastLine: string;
    /** Parsed from pnpm's ndjson stage events; null when none arrived. */
    phase: ProgressPhase;
    /** Distinct packages resolved/fetched so far. */
    done: number;
    total: number | null;
    currentPackage: string | null;
    downloaded: number | null;
    size: number | null;
    /** True when structured ndjson progress has been observed. */
    ndjson: boolean;
    /** Last fatal error from the stream (only meaningful after a failure). */
    error: string | null;
    /** True from the moment the user asks to cancel until the run ends. */
    cancelling: boolean;
}
/** Singleton progress state; the status route reads it, runDshPlugin writes it. */
export declare const progress: InstallProgress;
/** Identifies this host process; the client scopes its pending-restart flags to it. */
export declare const BOOT_ID: string;
/**
 * Central allowlist for every spawn target, regardless of which route built
 * it (defense in depth on top of per-route validation — the win32 bare-dsh
 * fallback runs through a shell). Suggested in #16 by @anupamme.
 *
 * `^`, `~` and `=` are intentionally allowed: restore/install flows turn
 * manifest specs such as "dsh-better-sidebar": "^0.14.0" into targets like
 * `dsh-better-sidebar@^0.14.0`, and regex-valid semver ranges must not be
 * mistaken for shell injection (whitespace and shell metacharacters remain
 * rejected — the win32 bare-dsh fallback is the reason to keep them out).
 */
export declare const TARGET_RE: RegExp;
/** Run one `dsh plugin --profile <p> …` command with timeout and progress tracking. */
export declare function runDshPlugin(profile: string, pluginArgs: string[]): Promise<InstallResult>;
/**
 * Adapt DSH Desktop's generation-scoped package manager to the existing
 * market runner. There is no runtime import or dependency on Desktop: the
 * Host supplies this public service only when the package is mounted there.
 */
export declare function createDesktopPluginRuntime(service: DesktopPnpmLike, activeProfileDir: string, invokingDir?: string, timeoutMs?: number): DesktopPluginRuntime;

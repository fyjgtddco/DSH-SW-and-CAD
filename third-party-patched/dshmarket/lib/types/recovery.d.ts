/**
 * The recovery surface a failed market restart leaves behind.
 *
 * Why this exists: a restart button is only as good as what happens when the
 * replacement does not come up. DSH's boot is all-or-nothing — the loader
 * mounts every entry, then `assertEntriesActivated` throws for the first one
 * that failed, so ONE broken plugin means `dsh web` exits and the browser is
 * left holding a dead origin. The market's own UI dies with the host it was
 * serving from, and the only way back used to be editing cordis.patch.yml by
 * hand.
 *
 * So the detached restart helper (src/restart.ts) hands off to THIS module
 * when the replacement it spawned never bound the port. This process is not
 * part of the DSH tree, does not need the tree to load, and takes over the
 * very origin the browser is already polling — which is what lets the market
 * page still open in that tab offer a way out instead of a timeout, and lets
 * a fresh visit to the URL find one too.
 *
 * What it does NOT do: it never boots DSH itself, never edits anything but
 * the profile's own patch layer (plus `dsh.profile.bundles` for a
 * disable-carrier), and never talks to the network. Its whole job is to take
 * the user's choice of "which plugins should be on at the next start", write
 * it through the same durable path the market's own toggles use, and then get
 * out of the way so the real host can bind the port.
 *
 * Security posture: loopback only, same-origin enforced on every mutating
 * request (a cross-site form post carries a foreign Origin and is refused),
 * and the write set is not free input — only the plugin names this process
 * was handed when the restart was scheduled can be switched.
 */
import { type Server } from 'node:http';
/** One installed plugin as the recovery surface is allowed to see it. */
export interface RecoveryPlugin {
    /** The installed package name — the only identity a write accepts. */
    name: string;
    /** Loader row ids this package owns in the profile patch layer. */
    rows: string[];
    /** Whether it stands enabled for the next boot. */
    enabled: boolean;
    /** Host infrastructure: switching it off breaks the chain itself. */
    protected: boolean;
    /** A bundle whose patch disables rows it does not own (see the toggle route). */
    carrier: boolean;
    /** Whether this surface may switch it at all. */
    toggleable: boolean;
    /** Why not, when `toggleable` is false; shown instead of a checkbox. */
    note?: string;
}
/**
 * Everything the recovery process needs, written by the market BEFORE the
 * restart (so it survives the host it describes) and read by the helper's
 * handoff.
 */
export interface RecoveryConfig {
    /** The origin the browser is polling; null when it could not be read. */
    port: number | null;
    profile: string;
    profileDir: string;
    patchPath: string;
    /** Boot id of the process that scheduled the restart. */
    bootId: string;
    marketVersion: string;
    scheduledAt: string;
    logs: {
        out: string;
        err: string;
    };
    /** The replacement invocation, already platform-resolved (see respawnInvocation). */
    spawn: {
        file: string;
        args: string[];
        viaShell: boolean;
        detached: boolean;
    };
    cwd: string;
    plugins: RecoveryPlugin[];
    /** Overridable for tests and for operators who want the port back sooner. */
    idleTimeoutMs?: number;
    /** How long a boot must keep answering before it counts as up (see BOOT_SETTLE_MS). */
    settleMs?: number;
}
/** One plugin the boot refused to activate, as the log named it. */
export interface BootFailureEntry {
    /** The loader entry name, verbatim — not yet matched to a package. */
    name: string;
    /** The reason the log gave, on one line. */
    reason: string;
    kind: 'failed' | 'pending' | 'unresolved';
}
/** What a failed boot's log says, reduced to what the UI can act on. */
export interface BootFailure {
    /** One line naming the shape of the failure. */
    summary: string;
    entries: BootFailureEntry[];
    /** The tail of the log, for the "what exactly happened" block. */
    tail: string;
}
/** What one plugin's switch did (or why it could not). */
export interface RecoveryChange {
    name: string;
    from: boolean;
    to: boolean;
    error?: string;
}
/**
 * Read the last failure DSH wrote into the replacement's log.
 *
 * The shapes come from @deepseek-ai/dsh-app-boot, which is the only thing
 * that writes them: `assertEntriesLoaded` names unresolved modules as
 * `plugin(s) failed to load: a, b`, and `assertEntriesActivated` prints
 * `N entries did not activate` followed by one `<entry>: <reason>` line per
 * failure — each reason being a stack, so every continuation line is
 * indented. `boot()` then wraps the whole thing, and the CLI's fail-loud
 * handler writes that wrapper to stderr, which the helper redirected to a
 * file. Parsing the LAST marker is deliberate: after a recovery restart
 * succeeds in writing a fix and the replacement fails again, the log holds
 * both failures and only the newest one describes the tree as it stands.
 *
 * Anything unrecognized degrades to the log tail with no named entries,
 * because "we could not tell which plugin" must not read as "no plugin
 * failed".
 * @param text - the replacement's captured stderr (and stdout), verbatim.
 * @returns the failure, with entries matched to names rather than packages.
 */
export declare function parseBootFailure(text: string): BootFailure;
/**
 * Match the names a failure used to the plugins a write can name.
 *
 * DSH reports the LOADER entry name, which is the package name for a bundle
 * row and a path for a local include; the market's inventory is keyed by
 * package name and row id. The three comparisons below are the shapes that
 * actually occur: an exact package name, an exact row id (a package may
 * insert a row under another name), and a path whose basename without
 * extension is the package. Unmatched names are returned as well, so the UI
 * can say "DSH named this one and we cannot switch it" rather than silently
 * dropping the only clue the user has.
 * @param plugins - the inventory handed to this process.
 * @param failure - the parsed failure.
 * @returns implicated plugin names and the names that matched nothing.
 */
export declare function matchFailureToPlugins(plugins: readonly RecoveryPlugin[], failure: BootFailure): {
    implicated: Set<string>;
    reasons: Map<string, BootFailureEntry>;
    unmatched: BootFailureEntry[];
};
/** The plugin view the UI renders: inventory plus what the failure implicated. */
export interface RecoveryPluginView extends RecoveryPlugin {
    /**
     * The switch position the surface OPENS on, not the plugin's current state.
     * For a plugin this boot blamed — and that can be switched at all — the fix
     * is "off", so the box starts off: that is the contract the failure prompt
     * makes ("the plugins DSH blamed are marked red and left unticked"), and
     * leaving the user to untick a red row by hand is a weaker one.
     */
    enabled: boolean;
    implicated: boolean;
    /** The reason the boot named, when it named this plugin. */
    reason?: string;
}
/** The whole payload both surfaces (the live page and the standalone one) read. */
export interface RecoveryPayload {
    ok: true;
    recovery: true;
    profile: string;
    bootId: string;
    scheduledAt: string;
    marketVersion: string;
    failure: BootFailure;
    plugins: RecoveryPluginView[];
    /** Names DSH blamed that this surface cannot switch. */
    unmatched: BootFailureEntry[];
    /** Write errors from the last apply, when the choice could not be written in full. */
    lastErrors: string[];
    logPath: string;
}
/**
 * Build the payload from the config and the parsed failure.
 * @param config - the recovery config written before the restart.
 * @param failure - the failure parsed from the replacement's log.
 * @param lastErrors - write errors from the previous apply, if it did not land.
 * @returns the payload both surfaces render.
 */
export declare function recoveryPayload(config: RecoveryConfig, failure: BootFailure, lastErrors?: readonly string[]): RecoveryPayload;
/**
 * Write one desired enable set through the durable layer.
 *
 * `enabled` is the FULL desired state, not a delta: the UI sends what its
 * checkboxes say, and only the differences are written. That is what makes
 * "uncheck everything I just installed, keep the rest" one request, and it is
 * also why a plugin the UI did not mention keeps whatever it had.
 *
 * A disable-carrier moves in and out of `dsh.profile.bundles` as well, the
 * same way the market's toggle route does it (its rows are not the only
 * effect it has). Failures are collected rather than thrown: a partial write
 * must be reported, not swallowed, and the caller then refuses to restart
 * into a composition it could not fully express.
 * @param config - the recovery config.
 * @param enabled - names to leave enabled; every other toggleable plugin is disabled.
 * @returns the changes attempted and the errors, if any.
 */
export declare function applyRecovery(config: RecoveryConfig, enabled: readonly string[]): Promise<{
    ok: boolean;
    changes: RecoveryChange[];
    errors: string[];
}>;
/** Probe one loopback port the way the restart helper does (connect, not bind). */
export declare function portListening(port: number, timeoutMs?: number): Promise<boolean>;
/**
 * Start the replacement and watch whether it takes the port.
 *
 * The caller has already released the listener by the time this runs — the
 * replacement cannot bind otherwise. A child that exits is a verdict on its
 * own and returns immediately: waiting out the full timeout for a process
 * that is already gone is the difference between a recovery page in five
 * seconds and one in forty-five, and the market page's own poll has a
 * deadline, and only once the answer has been STEADY — see BOOT_SETTLE_MS.
 * @param config - the recovery config (spawn invocation and port).
 * @returns whether the replacement really came up.
 */
export declare function respawnAndWatch(config: RecoveryConfig): Promise<boolean>;
/** Bilingual copy for the standalone page (the market page has its own). */
declare const PAGE_TEXT: {
    readonly zh: {
        readonly title: 'DeepSeek Harness 启动失败';
        readonly lead: '市场触发的重启没能起来，dsh 已退出。下面勾选下次启动要启用的插件——报错的插件已经标红并取消勾选。';
        readonly failed: '本次启动报错的插件';
        readonly plugins: '已安装插件（勾选 = 下次启动启用）';
        readonly protected: '宿主基础设施，不能关闭';
        readonly notToggleable: '无法在此关闭';
        readonly none: '没有解析到具体是哪个插件报的错，下面是 dsh 的原始输出。';
        readonly unmatched: 'DSH 点名但本页无法关闭的条目：';
        readonly apply: '保存并重启';
        readonly applying: '正在写入并重启…';
        readonly restarting: '重启中：dsh 起来后本页会自动刷新；若又失败，会带着新的报错回到这里。';
        readonly release: '释放端口并退出';
        readonly log: '完整日志';
        readonly error: '操作失败';
        readonly idle: '长时间无人操作后本页会自动释放端口。';
        readonly changes: '将要改动：';
        readonly nothing: '没有需要改动的插件，直接重启。';
        readonly writeFailed: '上次的选择没能完整写入，因此没有重启：';
    };
    readonly en: {
        readonly title: 'DeepSeek Harness failed to start';
        readonly lead: 'The restart the market triggered never came up, and dsh has exited. Tick the plugins to enable at the next start — the ones the boot blamed are highlighted in red and left unticked.';
        readonly failed: 'Blamed by this boot';
        readonly plugins: 'Installed plugins (ticked = enabled at the next start)';
        readonly protected: 'host infrastructure — cannot be switched off';
        readonly notToggleable: 'cannot be switched off here';
        readonly none: 'The log did not name a plugin; the raw output is below.';
        readonly unmatched: 'Named by DSH but not switchable here:';
        readonly apply: 'Save and restart';
        readonly applying: 'Writing and restarting…';
        readonly restarting: 'Restarting: this page reloads itself once dsh is up; if it fails again you come back here with the new error.';
        readonly release: 'Release the port and quit';
        readonly log: 'Full log';
        readonly error: 'The operation failed';
        readonly idle: 'This page releases the port on its own after a long idle period.';
        readonly changes: 'Changes:';
        readonly nothing: 'Nothing to change — restarting.';
        readonly writeFailed: 'The last choice could not be written in full, so nothing was restarted:';
    };
};
type PageLang = keyof typeof PAGE_TEXT;
/**
 * The standalone recovery page.
 *
 * A separate document rather than a React tree on purpose: the market's
 * bundle lives in the composition that just failed to boot, so the one thing
 * this page must not depend on is the composition. It reads its state from
 * the JSON endpoints and renders the checklist with the same colours as the
 * market page.
 * @param lang - which copy to serve.
 */
export declare function recoveryPageHtml(lang: PageLang): string;
/**
 * What the person looking at the surface decided.
 *
 * The enable set travels WITH the decision, because the process that shows
 * the checklist is not the process that writes it: `runRecovery` does the
 * single patch-layer write the user asked for, and it can only do that if the
 * ticks reach it. (They did not, once: the surface recorded the choice, went
 * straight to booting the unchanged composition, and came back with the same
 * error — the demo plugin's run is what exposed it.)
 */
export type RecoveryDecision = {
    kind: 'apply';
    enabled: string[];
} | {
    kind: 'released';
} | {
    kind: 'idle';
};
/** The parts of the recovery server a test (or the CLI) needs to drive it. */
export interface RecoveryServer {
    server: Server;
    port: number;
    url: string;
    close: () => Promise<void>;
    /**
     * Resolves when the surface is done being shown: the user asked for the
     * chosen composition to be written and booted (with the enable set they
     * left ticked), asked for the port back, or simply walked away.
     */
    finished: Promise<RecoveryDecision>;
}
/**
 * Serve the recovery surface on the port the failed host left behind.
 * @param config - the recovery config written before the restart.
 * @param failure - the failure parsed from the replacement's log.
 * @param options - test seams: an explicit port, and an idle timeout override.
 * @returns the running server and the outcome it settles on.
 */
export declare function startRecoveryServer(config: RecoveryConfig, failure: BootFailure, options?: {
    port?: number;
    idleTimeoutMs?: number;
    lastErrors?: readonly string[];
}): Promise<RecoveryServer>;
/**
 * The recovery process end to end: parse the log, serve the surface, and on a
 * user's decision write the choice down and try the boot again — rebinding
 * and starting over when the new composition fails too, because the second
 * failure is the one that says the first guess was wrong.
 * @param config - the recovery config written before the restart.
 * @param facts - what the helper observed (exit code, whether it ever bound).
 * @param options - test seams.
 * @returns the outcome, for the CLI's exit code.
 */
export declare function runRecovery(config: RecoveryConfig, facts?: {
    exitCode: number | null;
    bound: boolean;
}, options?: {
    port?: number;
    idleTimeoutMs?: number;
}): Promise<'booted' | 'released' | 'idle'>;
/**
 * The script entry: `node lib/recovery.js <config.json> [--exit=N] [--bound=0|1]`.
 * @param argv - arguments after the node executable and this script.
 */
export declare function runRecoveryCli(argv: readonly string[]): Promise<void>;
export {};

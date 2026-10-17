/**
 * HTTP routes bridging the browser market UI to the host. This layer only
 * parses requests, calls the service modules, and serializes responses —
 * process spawning lives in dsh-cli.ts, filesystem reads in profile.ts,
 * orchestration in install.ts / themes.ts / updates.ts.
 *
 * Security: the install route executes a shell command, so it accepts only
 * same-origin POSTs and only sources present in the curated registry.
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import { type PluginCommandRuntime } from './dsh-cli.ts';
import { type AgentsLookup } from './agents.ts';
import { type Channel } from './channels.ts';
import { type Region } from './regions.ts';
import { type LoaderEntry } from './themes.ts';
export type { LoaderEntry } from './themes.ts';
export type { UpdateStatus } from './updates.ts';
export interface WebServerService {
    register(route: {
        kind: 'exact' | 'prefix';
        path: string;
        handler: (request: IncomingMessage, response: ServerResponse) => void | Promise<void>;
    }): () => void;
}
export interface MarketHost {
    webServer: WebServerService;
    loader: {
        entries(): Iterable<LoaderEntry>;
    };
    plugin(plugin: unknown, config: unknown): {
        await(): Promise<unknown>;
        dispose(): Promise<unknown> | void;
    };
    on?(event: string, callback: (fiber: {
        entry?: {
            options?: {
                name?: string;
            };
        };
    }) => void): () => void;
    logger?: {
        info?(message: string): void;
        warn(message: string): void;
    };
}
/**
 * A host that owns activation for the whole composition.
 *
 * Some hosts watch the profile and replay it the moment the manifest lands
 * (measured against a bun-hmr watcher), which makes the market's own hot
 * mount a SECOND loader entry for an id the live composition already serves:
 * duplicate prefix routes, and a "restart required" verdict for a plugin that
 * is already up. Where the host publishes this bridge, the market asks it to
 * replay and reports what it answers, instead of mounting on its own.
 *
 * Optional by construction: absent on every host without the capability, and
 * named with `pluginActivation?` so a host that only knows `current` keeps
 * working unchanged.
 */
export interface HostPluginActivation {
    activate(): Promise<{
        ok: true;
    } | {
        ok: false;
        error: string;
    }>;
}
export interface MarketConfig {
    /** Profile the market installs into; matches the profile serving this UI. */
    profile: string;
    /** Host-authoritative profile directory; ordinary DSH derives it from DSH_HOME. */
    profileDirectory?: string;
    /** Installation-owned bundles live beside this host, outside the Desktop profile. */
    dshInstallDir?: string;
    /**
     * Whether a DSH Desktop shell serves this process — the shell owns the
     * window and the process lifecycle, which is what the capability bits mean
     * by "desktop".
     *
     * Kept separate from `profileDirectory` on purpose: since #639 the dsh
     * launcher hands every profile its own directory, so an explicit directory
     * no longer tells a desktop shell apart from an ordinary `dsh` run.
     */
    desktopHost?: boolean;
    /** Detached self-restart is unsafe under systemd/launchd/pm2; operators can disable it (#14). */
    allowRestart?: boolean;
    /** Which release channel the market offers ITSELF from; other plugins never follow it. */
    channel?: Channel;
    /** Which mirrors every outbound request uses; undefined until decided. */
    region?: Region;
    /** Snapshots retained per profile (issue #98); defaults to DEFAULT_MAX_SNAPSHOTS. */
    maxSnapshots?: number;
    /**
     * Environment variables pinned for plugin build/install commands (issue
     * #336): the compiler (CC/CXX) or anything else a native build reads, for
     * hosts whose dsh process cannot inherit a shell environment. These may
     * override values the parent process inherited, but never the PATH or CI
     * the market computes for its children.
     */
    buildEnv?: Record<string, string>;
}
export declare function marketVersion(): string;
/**
 * Register the market's HTTP routes.
 * @param host - Acquired webServer + shell services.
 * @param config - Validated market configuration.
 * @returns Disposer removing every registered route.
 */
export declare function mountMarketRoutes(host: MarketHost, config: MarketConfig, commandRuntime?: PluginCommandRuntime, agentsLookup?: AgentsLookup, hostActivation?: HostPluginActivation): () => void;

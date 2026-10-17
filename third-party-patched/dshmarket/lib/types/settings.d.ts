/**
 * The market's own settings namespace: the half that makes `allowRestart`
 * a switch on the plugin configuration page instead of a line the user has
 * to hand-write into cordis.yml.
 *
 * `allowRestart: false` is the documented answer for a host owned by
 * systemd, launchd or pm2 — a supervisor restarts it, so the market's
 * one-click restart must not launch a second one. Until now the only way to
 * say that was editing YAML in the right place with the right indentation,
 * where a stray space stops the profile booting.
 *
 * Only `allowRestart` is exposed. `profile` names which profile this
 * instance manages: it is decided at mount from the composition or the
 * command line, and a running instance cannot switch to another one, so
 * offering it as a field would promise something the write cannot deliver.
 *
 * `buildEnv` (issue #336) is deliberately NOT here. It is a map, so it
 * wants a form, and the market's own settings card is the form that renders
 * one — editing it there persists through the market's state.json and
 * reaches children through spawnEnv. Registering it here as well would make
 * this namespace a SECOND writer for a value that already has an editor:
 * the settings layer and the card would overwrite each other on their next
 * events, the exact shape of the channel bug documented below.
 *
 * The release channel is NOT here either, and that is a correction rather
 * than an omission. It was, briefly, and it made this namespace a second
 * writer for a value the market already stores in its own state.json: the
 * mount read the user's saved channel off disk, then `onChange` assigned
 * `source().channel` — which knows nothing about that file — straight back
 * over it. The choice survived exactly until the next settings event.
 *
 * Only a real host could show that; the unit lane mounts the routes without
 * this layer at all. `allowRestart` needs this door because its only other
 * one is hand-edited YAML. The channel has a control of its own on the
 * plugin configuration page, so a second door bought nothing and cost the
 * setting its memory.
 *
 * The wiring rides the scoped fiber, so a host with no settings service —
 * every dsh before 0.1.0-rc.7 — simply never runs any of this and the entry
 * configuration stands as composed. That is why this needs no version check
 * of its own.
 *
 * It depends on the SERVICE and nothing else, which is the whole point of
 * the shape below. This module used to import two convenience helpers,
 * `installSettingsSection` and `settingsNamespace`, from
 * `@deepseek-ai/dsh-settings`. dsh 0.1.2-alpha.1 deleted both — and a
 * missing NAMED EXPORT is not a missing service. `ctx.inject` degrades
 * quietly; an ESM named import that resolves to nothing is a SyntaxError at
 * module evaluation, which cordis's loader reports as a failed entry and the
 * host exits 1. Installing the market stopped the host from booting at all:
 *
 *   SyntaxError: The requested module '@deepseek-ai/dsh-settings' does not
 *   provide an export named 'installSettingsSection'
 *
 * The service itself did not change then — `sctx.settings.register(ns,
 * schema, { base })` is identical in 0.1.0-rc.7 and 0.1.2-alpha.2. Only the
 * two wrappers went away. So this inlines what the wrapper did (verified
 * against its source: an inject, a register, a watch, and an unload effect)
 * and validates the namespace here.
 *
 * It DID change in 0.1.7 (#677): `SettingsService` there has `describe` and
 * `update` and no `register` — namespaces are derived from a plugin's Config
 * schema instead. The `settings` service still exists, so the inject callback
 * runs and `register` threw a TypeError that cordis swallowed. Both entry
 * points now check for the method and, without it, leave the composed entry
 * standing and say so once in the host log. That is a stop-gap, not the
 * migration: on 0.1.7 the allowRestart switch is absent until the market
 * moves to the new model.
 */
import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
/** Namespace the card on the browser side keys itself to. */
export declare const MARKET_SETTINGS_NS = "dsh-market";
/**
 * What happened when the market offered its namespace to the host (#677).
 *
 * Reported rather than kept private because it is the difference between two
 * host generations, and nothing else in the system can state it: 0.1.7
 * derives settings from a plugin's Config schema and serves no third-party
 * namespace at all, so the market's plugin-configuration card cannot be
 * dispatched there. That is the host's model, not a profile defect — and a
 * test that cannot tell the two apart either demands a namespace a host
 * cannot serve, or passes while proving nothing.
 *
 * `pending` is the honest third answer: the market boots before the settings
 * service settles, so a reader can be asked too early to have an answer.
 */
export type SettingsNamespaceState = 'pending' | 'registered' | 'unsupported-by-host';
/** The state as of the last attempt to register the namespace. */
export declare function settingsNamespaceState(): SettingsNamespaceState;
/** The market settings a user may edit at runtime. */
export interface MarketSettings {
    allowRestart: boolean;
}
export declare const MarketSettings: z<MarketSettings>;
/** Serve the Desktop card without claiming settings-controlled restart. */
export declare function installDesktopMarketSettings(ctx: Context): void;
/**
 * Wire the namespace so a saved change reaches the routes immediately.
 *
 * The routes read `allowRestart` off this object on every request (the
 * status route reports the capability, the restart route enforces it), so
 * updating it in place is what makes a toggle take effect without a
 * restart — which would be a poor thing to require of a setting whose whole
 * subject is restarting.
 *
 * @param ctx - the plugin context owning the wiring.
 * @param resolved - the live config object the routes read.
 */
export declare function installMarketSettings(ctx: Context, resolved: {
    allowRestart?: boolean;
}): void;

/**
 * dsh-market host entry: mounts the market's HTTP routes once the profile
 * composes the webServer and shell services.
 */
import type { Context } from '@deepseek-ai/cordis';
import { type MarketConfig } from './routes.ts';
export declare const name = "dsh-market";
/** Optional cordis.yml configuration; profile defaults to `web`. */
export type Config = Partial<Pick<MarketConfig, 'profile' | 'allowRestart' | 'maxSnapshots' | 'buildEnv'>>;
/**
 * The deployment's declared authorities, read from the host (#729).
 *
 * DSH's own /api fence accepts loopback OR an authority the operator declared
 * (`dsh web --trusted-host <name>`, plus the LAN literals the CLI derives when
 * bound to 0.0.0.0). The market's routes are `exact` registrations on the bare
 * webServer, and exact matches win over that fence's prefix, so they never
 * pass through it — and a loopback-only rule of its own made every mutating
 * route 403 on any deployment reached by a name.
 *
 * The `connection` service is optional: a host without it leaves the fence
 * exactly as it was, which is the behaviour every release so far has had.
 * @returns a restore function for the effect that installed it.
 */
export declare function useTrustedHosts(ctx: Context): () => void;
export declare function apply(ctx: Context, config?: Config): void;

/**
 * Outbound HTTP for the market's own server-side calls.
 *
 * Node's global `fetch` ignores `HTTP_PROXY` / `HTTPS_PROXY` entirely
 * (measured on Node 25: a request with an unreachable proxy configured still
 * succeeds directly, and setting `NODE_USE_ENV_PROXY` at runtime changes
 * nothing — it is read at startup). On a machine whose route out is a local
 * proxy, that is not a slowdown but a different network: the catalog fetch
 * took 9.9s direct on a reporter's machine, seconds from the 15s timeout,
 * while their proxy sat unused a millisecond away.
 *
 * Every market request therefore calls undici's own fetch and carries a
 * dispatcher this module created. Two measurements say why, and they are
 * not the same fact:
 *
 * - On Node 25, `setGlobalDispatcher` from the undici package does not
 *   steer global fetch. With a dispatcher installed that way, a global
 *   fetch produced no CONNECT at a local proxy, while undici's own fetch
 *   produced `CONNECT awesome-dsh-plugin.com:443`.
 * - On Node 22 the two stacks share one symbol (#742). Global fetch reads
 *   `Symbol.for('undici.globalDispatcher.1')`. The host's first import of
 *   undici 8 (`web_fetch`) finds `.2` empty, installs its dispatcher, and
 *   writes a `Dispatcher1Wrapper` onto `.1`. After that, global fetch
 *   returns gzip bodies with null headers, and `JSON.parse` fails on the
 *   catalog. undici 7's fetch reads `.1` too, so calling it with no
 *   dispatcher fails the same way.
 *
 * The dispatcher is `EnvHttpProxyAgent` when a proxy is configured, and a
 * plain `Agent` otherwise. Only requests made here take it. The host's own
 * networking stays as the host configured it.
 */
/**
 * The proxy this process would use for the catalog, if any.
 *
 * The standard variables mirror `EnvHttpProxyAgent`'s own resolution
 * deliberately, rather than picking the order that reads best, because the
 * same answer does two jobs: it decides whether the request goes through
 * the proxy agent or the direct one, and it is what the failure message
 * CLAIMS was tried. A helper that
 * named a proxy undici would not have used would put a false statement in
 * every bug report. `npm_config_*` is an additional source on top of that:
 * npm holds its proxy in its own config namespace (a machine set up with
 * `npm config set proxy` has the proxy in `npm_config_proxy` and nowhere
 * else), and undici does not read it — so it is resolved here and handed to
 * the agent explicitly in `marketFetch`.
 *
 * Three details are undici's, not ours (env-http-proxy-agent.js):
 *   - lowercase wins over uppercase (`https_proxy ?? HTTPS_PROXY`)
 *   - an https request falls back to the http proxy when no https one is set
 *   - the value is tested for truthiness, so `HTTPS_PROXY=` — which is how
 *     people turn a proxy off — falls through instead of masking HTTP_PROXY
 *
 * Blank-is-unset is ours, and only widens that last one: undici would hand a
 * whitespace-only value to `new URL()` and throw out of the constructor.
 * Scheme-less host:port values are also common in Windows proxy fields and
 * npm config; proxy agents require URLs, so those default to `http://`.
 */
export declare function configuredProxy(): string | null;
/**
 * Fetch through the proxy this machine is configured to use, or directly
 * through this module's own agent when it has none. Both paths pass the
 * dispatcher described on this module.
 */
export declare function marketFetch(url: string, init?: {
    signal?: AbortSignal;
    headers?: Record<string, string>;
}): Promise<Response>;

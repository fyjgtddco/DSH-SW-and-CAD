/**
 * Minimal HTTP helpers shared by every market route: JSON serialization,
 * same-origin enforcement for mutating endpoints, and a size-capped JSON
 * body reader.
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
/** Write a JSON payload with no-store caching. */
export declare function sendJson(response: ServerResponse, status: number, payload: unknown): void;
/**
 * True when the request's Origin, IF IT HAS ONE, matches its Host — required
 * on every POST route.
 *
 * An absent Origin is allowed (#648). The check exists to stop another web
 * page from driving this loopback API, and the Fetch spec has browsers send
 * `Origin` on every POST — including same-origin and cross-origin form
 * submissions. A request that arrives WITHOUT it therefore did not come from
 * a page at all, and a non-browser client can set any Origin it likes, so
 * refusing the header's absence bought nothing while breaking a whole
 * supported host: the Desktop app's own proxy strips `origin`, `host`,
 * `cookie` and `sec-fetch-site` before forwarding to the in-process host and
 * injects a cookie only that host can sign. Every mutating request from the
 * Desktop build was answered 403 `untrusted origin`.
 *
 * What still holds after the change, and is the part worth keeping:
 *
 * - a cross-site page is refused — its Origin is present and different;
 * - `Origin: null` (a sandboxed iframe, a `data:` document) is refused, because
 *   `null` is a present-but-unparseable origin, not an absent one;
 * - a same-host-but-different-port request is refused;
 * - an EMPTY Origin is refused too: the header being present and unparseable
 *   is not the same statement as its being absent, and only absence is what
 *   a stripping proxy produces.
 *
 * The header's absence is a statement about the client, not about intent, and
 * the intent is the only thing this can judge.
 *
 * @param request - the incoming request.
 * @returns whether the request may mutate market state.
 */
/**
 * Whether a `Host` header names a loopback authority (#678).
 *
 * `Origin === Host` alone does not stop a DNS-rebinding page: the attacker
 * serves their page from `evil.com`, resolves that name to 127.0.0.1, and
 * the browser then connects to the loopback listener while sending
 * `Origin: http://evil.com` AND `Host: evil.com` — an equality that holds
 * for the attacker. `Host` is the one header the attack cannot forge, so it
 * is what has to name a loopback authority.
 *
 * `localhost` is included because browsers and RFC 6761 pin it to loopback;
 * a subdomain like `localhost.evil.com` does not match, and the port is
 * dropped before comparing.
 *
 * @param host - the request's `Host` header.
 * @returns whether it names a loopback authority.
 */
export declare function loopbackAuthority(host: string | undefined): boolean;
export declare function sameOrigin(request: IncomingMessage): boolean;
/** Read and parse a JSON request body, rejecting anything over 4 KiB. */
export declare function readJsonBody(request: IncomingMessage, maxBytes?: number): Promise<unknown>;

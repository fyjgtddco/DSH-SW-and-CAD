/** The official Electron profile is owned by DSH's in-process plugin manager. */
import { type DesktopPluginRuntime } from './dsh-cli.ts';
interface ManagedResult {
    application: string;
    error?: unknown;
    packageResult?: {
        exitCode: number | null;
        output?: string;
    };
}
export interface OfficialPluginManagerLike {
    installBundle(spec: string, options?: {
        requestId?: string;
    }): Promise<ManagedResult>;
    removeBundle(name: string): Promise<ManagedResult>;
    cancelInstall(requestId: string): Promise<unknown>;
}
/**
 * What a user can actually do when the market cannot perform an operation on
 * the official Desktop profile: the app's own Plugins page runs the same
 * managed pipeline. Bilingual, because it is read in the operations panel.
 */
export declare const OFFICIAL_PAGE_HINT = "\u5B98\u65B9\u684C\u9762\u5BA2\u6237\u7AEF\u7684\u8FD9\u4E2A\u63D2\u4EF6\u64CD\u4F5C\u9700\u8981\u5728\u300C\u8BBE\u7F6E \u2192 \u63D2\u4EF6\u300D\u91CC\u5B8C\u6210\uFF1B\u5E02\u573A\u65E0\u6CD5\u901A\u8FC7\u547D\u4EE4\u884C\u4FEE\u6539\u684C\u9762\u7AEF\u7684 profile\u3002 / On the official desktop app, do this from Settings \u2192 Plugins; the market cannot change the desktop profile through the command line.";
/** Never fall back to `dsh plugin --profile desktop`: that CLI is forbidden. */
export declare function createOfficialDesktopRuntime(managerLookup: () => OfficialPluginManagerLike | undefined, profileName: string, _profileDirectory?: string): DesktopPluginRuntime;
export {};

/** Bound asynchronous loader work without pretending that timeout cancels it. */
export declare class LifecycleWaitError extends Error {
}
/** Never start a second mutation on a loader object whose earlier work is pending. */
export declare function waitForLifecycle<T>(key: object, start: () => T | Promise<T>, label: string): Promise<T>;

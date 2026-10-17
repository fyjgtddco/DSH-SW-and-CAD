/** Bound asynchronous loader work without pretending that timeout cancels it. */
export class LifecycleWaitError extends Error {
}
const pending = new WeakMap();
const WAIT_MS = 10_000;
/** Never start a second mutation on a loader object whose earlier work is pending. */
export async function waitForLifecycle(key, start, label) {
    if (pending.has(key))
        throw new LifecycleWaitError(`${label}: previous operation is still pending`);
    const operation = Promise.resolve().then(start);
    pending.set(key, operation);
    // Both handlers remain attached after timeout, including for late rejection.
    void operation.then(() => { if (pending.get(key) === operation)
        pending.delete(key); }, () => { if (pending.get(key) === operation)
        pending.delete(key); });
    let timer;
    try {
        return await Promise.race([
            operation,
            new Promise((_resolve, reject) => {
                timer = setTimeout(() => reject(new LifecycleWaitError(`${label}: did not settle within ${WAIT_MS / 1000}s; runtime state is uncertain`)), WAIT_MS);
            }),
        ]);
    }
    finally {
        clearTimeout(timer);
    }
}

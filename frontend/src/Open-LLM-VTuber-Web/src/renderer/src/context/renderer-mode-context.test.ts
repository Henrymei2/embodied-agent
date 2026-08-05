import { describe, expect, it, vi } from "vitest";
import { requestImmersiveSession } from "./renderer-mode-context";

describe("VR activation", () => {
  it("starts audio unlock and WebXR request before yielding", async () => {
    const calls: string[] = [];
    let finishUnlock: (() => void) | undefined;
    let finishSession: ((session: XRSession) => void) | undefined;
    const unlock = vi.fn(() => {
      calls.push("audio");
      return new Promise<void>((resolve) => { finishUnlock = resolve; });
    });
    const session = {} as XRSession;
    const xr = {
      requestSession: vi.fn(() => {
        calls.push("xr");
        return new Promise<XRSession>((resolve) => { finishSession = resolve; });
      }),
    } as unknown as Pick<XRSystem, "requestSession">;

    const pending = requestImmersiveSession(xr, unlock);
    expect(calls).toEqual(["audio", "xr"]);
    finishUnlock?.();
    finishSession?.(session);
    await expect(pending).resolves.toBe(session);
  });

  it("continues into VR when only audio unlock is rejected", async () => {
    const session = {} as XRSession;
    const xr = {
      requestSession: vi.fn(() => Promise.resolve(session)),
    } as unknown as Pick<XRSystem, "requestSession">;

    await expect(requestImmersiveSession(
      xr,
      () => Promise.reject(new DOMException("blocked", "NotAllowedError")),
    )).resolves.toBe(session);
  });
});

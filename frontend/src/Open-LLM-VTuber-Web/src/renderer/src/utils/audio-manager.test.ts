import { beforeEach, describe, expect, it, vi } from "vitest";
import { AudioManager } from "./audio-manager";

class FakeAudio extends EventTarget {
  src = "";

  preload = "";

  currentTime = 0;

  play = vi.fn(() => Promise.resolve());

  pause = vi.fn();

  load = vi.fn();

  setAttribute = vi.fn();

  removeAttribute = vi.fn();
}

describe("AudioManager", () => {
  let instance: FakeAudio;

  let audioManager: AudioManager;

  beforeEach(() => {
    instance = new FakeAudio();
    vi.stubGlobal("Audio", vi.fn(() => instance));
    audioManager = new AudioManager();
  });

  it("starts the unlock play call synchronously", async () => {
    const unlock = audioManager.unlock();
    expect(instance.play).toHaveBeenCalledOnce();
    await unlock;
    expect(instance.pause).toHaveBeenCalledOnce();
  });

  it("reports autoplay rejection without leaving playback pending", async () => {
    const rejection = new DOMException("blocked", "NotAllowedError");
    instance.play.mockRejectedValueOnce(rejection);
    const states: string[] = [];
    const unsubscribe = audioManager.subscribe((state) => states.push(state.state === "error" ? state.code : state.state));

    await expect(audioManager.unlock()).rejects.toBe(rejection);
    expect(states).toContain("autoplay-blocked");
    unsubscribe();
  });

  it("resolves queued playback when audio ends", async () => {
    const playback = audioManager.playBase64Wav("AAAA");
    await Promise.resolve();
    instance.dispatchEvent(new Event("ended"));
    await expect(playback).resolves.toBeUndefined();
  });

  it("resolves active playback when interrupted", async () => {
    const playback = audioManager.playBase64Wav("AAAA");
    audioManager.stopCurrentAudio();
    await expect(playback).resolves.toBeUndefined();
    expect(instance.pause).toHaveBeenCalled();
  });

  it("classifies media decode failures", async () => {
    const states: string[] = [];
    audioManager.subscribe((state) => states.push(state.state === "error" ? state.code : state.state));
    const playback = audioManager.playBase64Wav("invalid");
    instance.dispatchEvent(new Event("error"));

    await expect(playback).rejects.toThrow("decode-failed");
    expect(states).toContain("decode-failed");
  });

  it("reuses one media element for consecutive sentences", async () => {
    const first = audioManager.playBase64Wav("AAAA");
    instance.dispatchEvent(new Event("ended"));
    await first;
    const second = audioManager.playBase64Wav("BBBB");
    instance.dispatchEvent(new Event("ended"));
    await second;

    expect(Audio).toHaveBeenCalledOnce();
    expect(instance.play).toHaveBeenCalledTimes(2);
  });
});

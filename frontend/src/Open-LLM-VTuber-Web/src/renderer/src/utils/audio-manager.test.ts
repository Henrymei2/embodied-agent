import { beforeEach, describe, expect, it, vi } from "vitest";
import { AudioManager } from "./audio-manager";

class FakeAudio extends EventTarget {
  src = "";

  preload = "";

  currentTime = 0;

  duration = Number.NaN;

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
    await Promise.resolve();
    expect(audioManager.getPlaybackProgress()).not.toBeNull();
    audioManager.stopCurrentAudio();
    await expect(playback).resolves.toBeUndefined();
    expect(instance.pause).toHaveBeenCalled();
    expect(audioManager.getPlaybackProgress()).toBeNull();
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

  it("reports read-only timing for the active playback generation", async () => {
    expect(audioManager.getPlaybackProgress()).toBeNull();
    const playback = audioManager.playBase64Wav("AAAA");
    await Promise.resolve();
    instance.duration = 10;
    instance.currentTime = 2.5;

    expect(audioManager.getPlaybackProgress()).toEqual({
      generation: expect.any(Number),
      currentTime: 2.5,
      duration: 10,
      ratio: 0.25,
    });

    instance.dispatchEvent(new Event("ended"));
    await playback;
    expect(audioManager.getPlaybackProgress()).toBeNull();
  });

  it("starts at zero when media duration is not available", async () => {
    const playback = audioManager.playBase64Wav("AAAA");
    await Promise.resolve();
    instance.currentTime = 4;

    expect(audioManager.getPlaybackProgress()).toMatchObject({
      currentTime: 4,
      duration: null,
      ratio: 0,
    });

    instance.dispatchEvent(new Event("ended"));
    await playback;
  });

  it("assigns a new generation to each consecutive audio segment", async () => {
    const first = audioManager.playBase64Wav("AAAA");
    await Promise.resolve();
    const firstGeneration = audioManager.getPlaybackProgress()!.generation;
    instance.dispatchEvent(new Event("ended"));
    await first;

    const second = audioManager.playBase64Wav("BBBB");
    await Promise.resolve();
    const secondGeneration = audioManager.getPlaybackProgress()!.generation;
    expect(secondGeneration).toBeGreaterThan(firstGeneration);
    instance.dispatchEvent(new Event("ended"));
    await second;
  });
});

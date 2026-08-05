export type AudioPlaybackErrorCode =
  | "autoplay-blocked"
  | "decode-failed"
  | "empty-audio"
  | "playback-failed";

export type AudioPlaybackState =
  | { state: "idle" | "unlocked" | "playing" }
  | { state: "error"; code: AudioPlaybackErrorCode; message: string };

type AudioPlaybackListener = (state: AudioPlaybackState) => void;

// 44-byte PCM WAV header followed by one silent 16-bit sample. It is small
// enough to start synchronously from the Enter VR gesture without a fetch.
const SILENT_WAV = "data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA==";

function playbackError(error: unknown): Extract<AudioPlaybackState, { state: "error" }> {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return {
      state: "error",
      code: "autoplay-blocked",
      message: "Audio permission blocked. Exit VR and enter again.",
    };
  }
  return {
    state: "error",
    code: "playback-failed",
    message: "Voice playback failed.",
  };
}

export class AudioManager {
  private audio: HTMLAudioElement | null = null;

  private listeners = new Set<AudioPlaybackListener>();

  private playbackGeneration = 0;

  private finishActivePlayback: (() => void) | null = null;

  private state: AudioPlaybackState = { state: "idle" };

  private getAudio() {
    if (!this.audio) {
      this.audio = new Audio();
      this.audio.preload = "auto";
      this.audio.setAttribute("playsinline", "");
    }
    return this.audio;
  }

  private publish(state: AudioPlaybackState) {
    this.state = state;
    this.listeners.forEach((listener) => listener(state));
  }

  subscribe(listener: AudioPlaybackListener) {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Begin media playback during the same user gesture that requests WebXR.
   * The call to `play()` deliberately happens before this async method yields.
   */
  async unlock(): Promise<void> {
    const audio = this.getAudio();
    audio.src = SILENT_WAV;
    audio.load();
    try {
      await audio.play();
      audio.pause();
      audio.currentTime = 0;
      this.publish({ state: "unlocked" });
    } catch (error) {
      const state = playbackError(error);
      console.error(`[AudioManager] ${state.code}`);
      this.publish(state);
      throw error;
    }
  }

  async playBase64Wav(audioBase64: string): Promise<void> {
    if (!audioBase64) {
      const state: AudioPlaybackState = {
        state: "error",
        code: "empty-audio",
        message: "The voice response contained no audio.",
      };
      this.publish(state);
      throw new Error(state.code);
    }

    this.stopCurrentAudio(false);
    const audio = this.getAudio();
    const generation = ++this.playbackGeneration;

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const cleanup = () => {
        audio.removeEventListener("ended", handleEnded);
        audio.removeEventListener("error", handleMediaError);
        if (this.finishActivePlayback === finishStopped) this.finishActivePlayback = null;
      };
      const finish = (error?: unknown) => {
        if (settled) return;
        settled = true;
        cleanup();
        if (generation !== this.playbackGeneration) {
          resolve();
          return;
        }
        if (error) reject(error); else resolve();
      };
      const finishStopped = () => finish();
      const handleEnded = () => {
        this.publish({ state: "idle" });
        finish();
      };
      const handleMediaError = () => {
        const state: AudioPlaybackState = {
          state: "error",
          code: "decode-failed",
          message: "The voice audio could not be decoded.",
        };
        console.error(`[AudioManager] ${state.code}`);
        this.publish(state);
        finish(new Error(state.code));
      };

      this.finishActivePlayback = finishStopped;
      audio.addEventListener("ended", handleEnded, { once: true });
      audio.addEventListener("error", handleMediaError, { once: true });
      audio.src = `data:audio/wav;base64,${audioBase64}`;
      audio.load();
      const playPromise = audio.play();
      playPromise.then(() => {
        if (generation === this.playbackGeneration) this.publish({ state: "playing" });
      }).catch((error) => {
        const state = playbackError(error);
        console.error(`[AudioManager] ${state.code}`);
        this.publish(state);
        finish(error);
      });
    });
  }

  stopCurrentAudio(publishIdle = true) {
    const audio = this.audio;
    if (!audio) return;
    this.playbackGeneration += 1;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    const finish = this.finishActivePlayback;
    this.finishActivePlayback = null;
    finish?.();
    if (publishIdle) this.publish({ state: "idle" });
  }

  hasCurrentAudio(): boolean {
    return this.state.state === "playing";
  }
}

export const audioManager = new AudioManager();

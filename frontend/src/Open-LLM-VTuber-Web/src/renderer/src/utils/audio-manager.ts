/**
 * Global audio manager for handling audio playback and interruption
 * This ensures all components share the same audio reference
 */
class AudioManager {
  private currentAudio: HTMLAudioElement | null = null;

  /**
   * Set the current playing audio
   */
  setCurrentAudio(audio: HTMLAudioElement) {
    this.currentAudio = audio;
  }

  /**
   * Stop current audio playback
   */
  stopCurrentAudio() {
    if (this.currentAudio) {
      console.log('[AudioManager] Stopping current audio');
      const audio = this.currentAudio;
      
      audio.pause();
      audio.src = '';
      audio.load();

      this.currentAudio = null;
    } else {
      console.log('[AudioManager] No current audio playing to stop.');
    }
  }

  /**
   * Clear the current audio reference (called when audio ends naturally)
   */
  clearCurrentAudio(audio: HTMLAudioElement) {
    if (this.currentAudio === audio) {
      this.currentAudio = null;
    }
  }

  /**
   * Check if there's currently playing audio
   */
  hasCurrentAudio(): boolean {
    return this.currentAudio !== null;
  }
}

// Export singleton instance
export const audioManager = new AudioManager();

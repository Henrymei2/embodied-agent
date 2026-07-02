/* eslint-disable func-names */
/* eslint-disable no-underscore-dangle */
/* eslint-disable @typescript-eslint/ban-ts-comment */
import { useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAiState } from '@/context/ai-state-context';
import { useSubtitle } from '@/context/subtitle-context';
import { useChatHistory } from '@/context/chat-history-context';
import { audioTaskQueue } from '@/utils/task-queue';
import { audioManager } from '@/utils/audio-manager';
import { toaster } from '@/components/ui/toaster';
import { useWebSocket } from '@/context/websocket-context';
import { DisplayText } from '@/services/websocket-service';

interface AudioTaskOptions {
  audioBase64: string
  volumes: number[]
  sliceLength: number
  displayText?: DisplayText | null
  speaker_uid?: string
  forwarded?: boolean
}

/**
 * Custom hook for handling audio playback tasks
 */
export const useAudioTask = () => {
  const { t } = useTranslation();
  const { aiState, backendSynthComplete, setBackendSynthComplete } = useAiState();
  const { setSubtitleText } = useSubtitle();
  const { appendResponse, appendAIMessage } = useChatHistory();
  const { sendMessage } = useWebSocket();

  // State refs to avoid stale closures
  const stateRef = useRef({
    aiState,
    setSubtitleText,
    appendResponse,
    appendAIMessage,
  });

  stateRef.current = {
    aiState,
    setSubtitleText,
    appendResponse,
    appendAIMessage,
  };

  /**
   * Stop current audio playback (delegates to global audioManager)
   */
  const stopCurrentAudio = useCallback(() => {
    audioManager.stopCurrentAudio();
  }, []);

  /**
   * Handle audio playback
   */
  const handleAudioPlayback = (options: AudioTaskOptions): Promise<void> => new Promise((resolve) => {
    const {
      aiState: currentAiState,
      setSubtitleText: updateSubtitle,
      appendResponse: appendText,
      appendAIMessage: appendAI,
    } = stateRef.current;

    // Skip if already interrupted
    if (currentAiState === 'interrupted') {
      console.warn('Audio playback blocked by interruption state.');
      resolve();
      return;
    }

    const { audioBase64, displayText, forwarded } = options;

    // Update display text
    if (displayText) {
      appendText(displayText.text);
      appendAI(displayText.text, displayText.name, displayText.avatar);
      if (audioBase64) {
        updateSubtitle(displayText.text);
      }
      if (!forwarded) {
        sendMessage({
          type: "audio-play-start",
          display_text: displayText,
          forwarded: true,
        });
      }
    }

    try {
      // Process audio if available
      if (audioBase64) {
        const audioDataUrl = `data:audio/wav;base64,${audioBase64}`;
        const audio = new Audio(audioDataUrl);
        
        audioManager.setCurrentAudio(audio);
        let isFinished = false;

        const cleanup = () => {
          audioManager.clearCurrentAudio(audio);
          if (!isFinished) {
            isFinished = true;
            resolve();
          }
        };

        audio.addEventListener('canplaythrough', () => {
          // Check for interruption before playback
          if (stateRef.current.aiState === 'interrupted' || !audioManager.hasCurrentAudio()) {
            console.warn('Audio playback cancelled due to interruption or audio was stopped');
            cleanup();
            return;
          }

          console.log('Starting audio playback');
          audio.play().catch((err) => {
            console.error("Audio play error:", err);
            cleanup();
          });
        });

        audio.addEventListener('ended', () => {
          console.log("Audio playback completed");
          cleanup();
        });

        audio.addEventListener('error', (error) => {
          console.error("Audio playback error:", error);
          cleanup();
        });

        audio.load();
      } else {
        resolve();
      }
    } catch (error) {
      console.error('Audio playback setup error:', error);
      toaster.create({
        title: `${t('error.audioPlayback')}: ${error}`,
        type: "error",
        duration: 2000,
      });
      resolve();
    }
  });

  // Handle backend synthesis completion
  useEffect(() => {
    let isMounted = true;

    const handleComplete = async () => {
      await audioTaskQueue.waitForCompletion();
      if (isMounted && backendSynthComplete) {
        stopCurrentAudio();
        sendMessage({ type: "frontend-playback-complete" });
        setBackendSynthComplete(false);
      }
    };

    handleComplete();

    return () => {
      isMounted = false;
    };
  }, [backendSynthComplete, sendMessage, setBackendSynthComplete, stopCurrentAudio]);

  /**
   * Add a new audio task to the queue
   */
  const addAudioTask = async (options: AudioTaskOptions) => {
    const { aiState: currentState } = stateRef.current;

    if (currentState === 'interrupted') {
      console.log('Skipping audio task due to interrupted state');
      return;
    }

    console.log(`Adding audio task ${options.displayText?.text} to queue`);
    audioTaskQueue.addTask(() => handleAudioPlayback(options));
  };

  return {
    addAudioTask,
    appendResponse,
    stopCurrentAudio,
  };
};

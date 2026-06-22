/* eslint-disable global-require */
/* eslint-disable @typescript-eslint/no-var-requires */
/* eslint-disable no-use-before-define */
import { Subject } from 'rxjs';
import { ModelInfo } from '@/context/live2d-config-context';
import { HistoryInfo } from '@/context/websocket-context';
import { ConfigFile } from '@/context/character-config-context';
import { DynamicStageDocument } from '@/context/dynamic-stage-context';
import { toaster } from '@/components/ui/toaster';

export interface DisplayText {
  text: string;
  name: string;
  avatar: string;
}

interface BackgroundFile {
  name: string;
  url: string;
}

type WebSocketState = 'CONNECTING' | 'OPEN' | 'CLOSING' | 'CLOSED';

export interface AudioPayload {
  type: 'audio';
  audio?: string;
  volumes?: number[];
  slice_length?: number;
  display_text?: DisplayText;
  actions?: Actions;
}

export interface Message {
  id: string;
  content: string;
  role: "ai" | "human";
  timestamp: string;
  name?: string;
  avatar?: string;

  // Fields for different message types (make optional)
  type?: 'text' | 'tool_call_status'; // Add possible types, default to 'text' if omitted
  tool_id?: string; // Specific to tool calls
  tool_name?: string; // Specific to tool calls
  status?: 'running' | 'completed' | 'error'; // Specific to tool calls
}

export interface Actions {
  expressions?: string[] | number [];
  pictures?: string[];
  sounds?: string[];
}

export interface MessageEvent {
  request_id?: string;
  tool_id: any;
  tool_name: any;
  name: any;
  status: any;
  content: string;
  timestamp: string;
  type: string;
  action?: string;
  args?: Record<string, unknown>;
  audio?: string;
  volumes?: number[];
  slice_length?: number;
  files?: BackgroundFile[];
  actions?: Actions;
  text?: string;
  model_info?: ModelInfo;
  conf_name?: string;
  conf_uid?: string;
  uids?: string[];
  messages?: Message[];
  history_uid?: string;
  success?: boolean;
  histories?: HistoryInfo[];
  configs?: ConfigFile[];
  message?: string;
  members?: string[];
  is_owner?: boolean;
  client_uid?: string;
  forwarded?: boolean;
  display_text?: DisplayText;
  live2d_model?: string;
  stage?: DynamicStageDocument;
  state?: DynamicStageDocument;
  result?: Record<string, unknown>;
  browser_view?: {
    debuggerFullscreenUrl: string;
    debuggerUrl: string;
    pages: {
      id: string;
      url: string;
      faviconUrl: string;
      title: string;
      debuggerUrl: string;
      debuggerFullscreenUrl: string;
    }[];
    wsUrl: string;
    sessionId?: string;
  };
}

interface BrowserWebSocketTestApi {
  receive: (message: Partial<MessageEvent>, timeoutMs?: number) => Promise<object | null>;
  send: (message: object) => void;
  sent: () => object[];
  clearSent: () => void;
  state: () => WebSocketState;
  examples: {
    moveToCalendar: () => Promise<object | null>;
    changeStageBackground: () => Promise<object | null>;
    addStageText: () => Promise<object | null>;
    resetStage: () => Promise<object | null>;
    getCalendarPosition: () => Promise<object | null>;
    listObjects: () => Promise<object | null>;
    getSceneStatus: () => Promise<object | null>;
  };
}

declare global {
  interface Window {
    wsTest?: BrowserWebSocketTestApi;
  }
}

// Get translation function for error messages
const getTranslation = () => {
  try {
    const i18next = require('i18next').default;
    return i18next.t.bind(i18next);
  } catch (e) {
    // Fallback if i18next is not available
    return (key: string) => key;
  }
};

class WebSocketService {
  private static instance: WebSocketService;

  private ws: WebSocket | null = null;

  private messageSubject = new Subject<MessageEvent>();

  private stateSubject = new Subject<WebSocketState>();

  private outboundSubject = new Subject<object>();

  private sentMessages: object[] = [];

  private currentState: WebSocketState = 'CLOSED';

  private isTestingInjectedMessage = false;

  static getInstance() {
    if (!WebSocketService.instance) {
      WebSocketService.instance = new WebSocketService();
    }
    return WebSocketService.instance;
  }

  private initializeConnection() {
    this.sendMessage({
      type: 'fetch-backgrounds',
    });
    this.sendMessage({
      type: 'fetch-configs',
    });
    this.sendMessage({
      type: 'fetch-history-list',
    });
    this.sendMessage({
      type: 'create-new-history',
    });
  }

  connect(url: string) {
    if (this.ws?.readyState === WebSocket.CONNECTING ||
        this.ws?.readyState === WebSocket.OPEN) {
      this.disconnect();
    }

    try {
      this.ws = new WebSocket(url);
      this.currentState = 'CONNECTING';
      this.stateSubject.next('CONNECTING');

      this.ws.onopen = () => {
        this.currentState = 'OPEN';
        this.stateSubject.next('OPEN');
        this.initializeConnection();
      };

      this.ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          this.messageSubject.next(message);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
          toaster.create({
            title: `${getTranslation()('error.failedParseWebSocket')}: ${error}`,
            type: "error",
            duration: 2000,
          });
        }
      };

      this.ws.onclose = () => {
        this.currentState = 'CLOSED';
        this.stateSubject.next('CLOSED');
      };

      this.ws.onerror = () => {
        this.currentState = 'CLOSED';
        this.stateSubject.next('CLOSED');
      };
    } catch (error) {
      console.error('Failed to connect to WebSocket:', error);
      this.currentState = 'CLOSED';
      this.stateSubject.next('CLOSED');
    }
  }

  sendMessage(message: object) {
    const suppressClosedWarning = this.isTestingInjectedMessage;
    this.sentMessages.push(message);
    this.outboundSubject.next(message);

    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else if (suppressClosedWarning) {
      console.log('[wsTest] WebSocket is not open; captured message without sending.');
    } else {
      console.warn('WebSocket is not open. Unable to send message:', message);
      toaster.create({
        title: getTranslation()('error.websocketNotOpen'),
        type: 'error',
        duration: 2000,
      });
    }
  }

  injectMessageForTesting(message: Partial<MessageEvent>, timeoutMs = 1500) {
    const normalizedMessage = message as MessageEvent;
    console.log('[wsTest] Injecting inbound WebSocket message:', normalizedMessage);

    return new Promise<object | null>((resolve) => {
      let settled = false;
      let timeoutId: number;
      const subscription = this.outboundSubject.subscribe((outboundMessage) => {
        if (settled) {
          return;
        }
        settled = true;
        subscription.unsubscribe();
        window.clearTimeout(timeoutId);
        this.isTestingInjectedMessage = false;
        console.log('[wsTest] Captured outbound WebSocket message:', outboundMessage);
        resolve(outboundMessage);
      });

      timeoutId = window.setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        subscription.unsubscribe();
        this.isTestingInjectedMessage = false;
        console.log('[wsTest] No outbound WebSocket message captured.');
        resolve(null);
      }, timeoutMs);

      this.isTestingInjectedMessage = true;
      this.messageSubject.next(normalizedMessage);
    });
  }

  getSentMessages() {
    return [...this.sentMessages];
  }

  clearSentMessages() {
    this.sentMessages = [];
  }

  onMessage(callback: (message: MessageEvent) => void) {
    return this.messageSubject.subscribe(callback);
  }

  onStateChange(callback: (state: WebSocketState) => void) {
    return this.stateSubject.subscribe(callback);
  }

  disconnect() {
    this.ws?.close();
    this.ws = null;
  }

  getCurrentState() {
    return this.currentState;
  }
}

export const wsService = WebSocketService.getInstance();

if (typeof window !== 'undefined') {
  window.wsTest = {
    receive: (message, timeoutMs) => wsService.injectMessageForTesting(message, timeoutMs),
    send: (message) => wsService.sendMessage(message),
    sent: () => wsService.getSentMessages(),
    clearSent: () => wsService.clearSentMessages(),
    state: () => wsService.getCurrentState(),
    examples: {
      moveToCalendar: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'move_to',
        args: { target: 'calendar' },
      }, 2000),
      changeStageBackground: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'stage_patch_state',
        args: {
          patch: {
            canvas: {
              background: 'linear-gradient(180deg, #f8fafc 0%, #dbeafe 48%, #d1d5db 48%, #9ca3af 100%)',
            },
          },
        },
      }, 2000),
      addStageText: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'stage_patch_state',
        args: {
          patch: {
            nodes: [{
              id: `note-${Date.now()}`,
              type: 'text',
              label: 'note',
              text: 'agent note',
              x: 480,
              y: 120,
              width: 220,
              height: 48,
              zIndex: 6,
              style: {
                color: '#111827',
                fontSize: 24,
                fontWeight: '800',
              },
            }],
          },
        },
      }, 2000),
      resetStage: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'stage_reset_state',
        args: {},
      }, 2000),
      getCalendarPosition: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'get_object_position',
        args: { name: 'calendar' },
      }),
      listObjects: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'list_objects',
        args: {},
      }),
      getSceneStatus: () => wsService.injectMessageForTesting({
        type: 'frontend_action_request',
        request_id: `test-${Date.now()}`,
        action: 'get_scene_status',
        args: {},
      }),
    },
  };
  console.log('[wsTest] Browser console WebSocket test helper is available at window.wsTest');
}

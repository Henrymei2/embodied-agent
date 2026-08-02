/* eslint-disable global-require */
/* eslint-disable @typescript-eslint/no-var-requires */
/* eslint-disable no-use-before-define */
import { Subject } from 'rxjs';
import { HistoryInfo } from '@/context/websocket-context';
import { DynamicStageDocument } from '@/context/dynamic-stage-context';
import type {
  AgentActivityPhase,
  AgentActivityTool,
} from '@/context/agent-activity-context';
import { toaster } from '@/components/ui/toaster';
import type { PresenceReceipt } from '@/context/presence-context';
import type { VrStageDocument } from '@/context/vr-stage-context';

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
  browser_view?: MessageEvent['browser_view'];
}

export interface Actions {
  pictures?: string[];
  sounds?: string[];
}

export interface MessageEvent {
  request_id?: string;
  message_id?: string;
  tool_id: any;
  tool_name: any;
  name: any;
  status: any;
  content: string;
  timestamp: string;
  type: string;
  phase?: AgentActivityPhase;
  tools?: AgentActivityTool[];
  action?: string;
  args?: Record<string, unknown>;
  audio?: string;
  volumes?: number[];
  slice_length?: number;
  files?: BackgroundFile[];
  actions?: Actions;
  text?: string;
  conf_name?: string;
  conf_uid?: string;
  uids?: string[];
  messages?: Message[];
  history_uid?: string;
  success?: boolean;
  histories?: HistoryInfo[];
  message?: string;
  members?: string[];
  is_owner?: boolean;
  client_uid?: string;
  forwarded?: boolean;
  display_text?: DisplayText;
  stage?: DynamicStageDocument | VrStageDocument;
  state?: DynamicStageDocument;
  result?: Record<string, unknown>;
  enabled?: boolean;
  attention_budget?: number;
  attention_budget_max?: number;
  attention_refill_per_hour?: number;
  attention_budget_remaining?: number;
  snoozed?: boolean;
  snoozed_until?: string | null;
  direction_affinity?: Record<string, number>;
  receipts?: PresenceReceipt[];
  receipt?: PresenceReceipt;
  receipt_id?: string;
  ok?: boolean;
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

  private currentHistoryUid: string | null = null;

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
      type: 'fetch-history-list',
    });
    this.sendMessage(this.currentHistoryUid
      ? { type: 'fetch-and-set-history', history_uid: this.currentHistoryUid }
      : { type: 'create-new-history' });
  }

  connect(url: string) {
    if (this.ws?.readyState === WebSocket.CONNECTING ||
        this.ws?.readyState === WebSocket.OPEN) {
      this.disconnect();
    }

    try {
      const socket = new WebSocket(url);
      this.ws = socket;
      this.currentState = 'CONNECTING';
      this.stateSubject.next('CONNECTING');

      socket.onopen = () => {
        if (this.ws !== socket) return;
        this.currentState = 'OPEN';
        this.stateSubject.next('OPEN');
        this.initializeConnection();
      };

      socket.onmessage = (event) => {
        if (this.ws !== socket) return;
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'new-history-created' && typeof message.history_uid === 'string') {
            this.currentHistoryUid = message.history_uid;
          }
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

      socket.onclose = (event) => {
        if (this.ws !== socket) return;
        this.ws = null;
        console.warn(`WebSocket closed (${event.code}): ${event.reason || 'no reason'}`);
        this.currentState = 'CLOSED';
        this.stateSubject.next('CLOSED');
      };

      socket.onerror = () => {
        if (this.ws !== socket) return;
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
    if (
      'type' in message && message.type === 'fetch-and-set-history' &&
      'history_uid' in message && typeof message.history_uid === 'string'
    ) {
      this.currentHistoryUid = message.history_uid;
    }
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

      const timeoutId = window.setTimeout(() => {
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
    const socket = this.ws;
    this.ws = null;
    socket?.close();
    this.currentState = 'CLOSED';
    this.stateSubject.next('CLOSED');
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

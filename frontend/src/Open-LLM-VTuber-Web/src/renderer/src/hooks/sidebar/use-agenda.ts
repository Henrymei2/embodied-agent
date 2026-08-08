import { useCallback, useEffect, useState } from 'react';
import { useWebSocket } from '@/context/websocket-context';

export interface AgendaEntry {
  id: string;
  kind: 'pursuit' | 'idle' | 'scheduled' | 'user';
  title: string;
  status: string;
  progress_summary: string;
  created_at: string;
  queue_position: number;
}

export interface AgendaSnapshot {
  revision: number;
  fencing_token: number;
  paused: boolean;
  active: AgendaEntry | null;
  entries: AgendaEntry[];
  health: {
    terminal: Record<'completed' | 'failed' | 'abandoned', number>;
    completion_rate: number | null;
    reports: Record<'pending' | 'delivering' | 'delivered' | 'failed', number>;
    events: Record<'pending' | 'claimed' | 'acknowledged' | 'dead_letter', number>;
    dead_letters: number;
    quota: {
      window_started_at: string;
      planner_calls: number;
      capability_calls: Record<string, number>;
    };
    planner: {
      consecutive_failures: number;
      circuit_open_until: string | null;
      last_error: string | null;
    };
  };
  runtime: {
    mode: 'live' | 'headless' | 'stopped';
    running: boolean;
    headless_enabled: boolean;
    clients: string[];
    instance_id: string | null;
    lease_status: 'held' | 'standby' | 'fenced';
    fencing_token: number | null;
    workspace_lease: {
      held: boolean;
      owner_id?: string;
      holder_label?: string;
      fencing_token?: number;
      remaining_seconds?: number;
      error?: string;
    };
    last_error: string | null;
  };
  event_producers: Record<'gmail' | 'google_calendar' | 'workspace', {
    status: 'running' | 'degraded' | 'disabled';
    last_success_at: string | null;
    last_error: string | null;
    consecutive_failures: number;
  }>;
  updated_at: string;
}

export interface AgendaExternalEvent {
  id: string;
  kind: string;
  summary: string;
  status: string;
  created_at: string;
  trigger_reference: null | {
    source: 'gmail' | 'google_calendar' | 'workspace';
    resource_type: string;
    resource_id: string;
    revision: string;
    change_type: string;
    simulated: boolean;
  };
}

export interface DemoFixture { id: string; label: string }

export function useAgenda() {
  const { baseUrl } = useWebSocket();
  const [snapshot, setSnapshot] = useState<AgendaSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [events, setEvents] = useState<AgendaExternalEvent[]>([]);
  const [fixtures, setFixtures] = useState<DemoFixture[]>([]);
  const endpoint = `${baseUrl.replace(/\/$/, '')}/api/agenda`;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [snapshotResponse, eventsResponse, fixturesResponse] = await Promise.all([
        fetch(endpoint, { cache: 'no-store' }),
        fetch(`${endpoint}/events?limit=8`, { cache: 'no-store' }),
        fetch(`${endpoint}/demo-fixtures`, { cache: 'no-store' }),
      ]);
      if (!snapshotResponse.ok) throw new Error(`agenda request failed: ${snapshotResponse.status}`);
      setSnapshot(await snapshotResponse.json() as AgendaSnapshot);
      if (eventsResponse.ok) {
        const payload = await eventsResponse.json() as { events: AgendaExternalEvent[] };
        setEvents(payload.events);
      }
      if (fixturesResponse.ok) {
        const payload = await fixturesResponse.json() as { fixtures: DemoFixture[] };
        setFixtures(payload.fixtures);
      }
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  const action = useCallback(async (path: string, method = 'POST') => {
    const response = await fetch(`${endpoint}${path}`, { method });
    if (!response.ok) throw new Error(`agenda action failed: ${response.status}`);
    await refresh();
  }, [endpoint, refresh]);

  const triggerFixture = useCallback(async (fixtureId: string) => {
    const requestId = globalThis.crypto?.randomUUID?.()
      ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const response = await fetch(`${endpoint}/demo-fixtures/${encodeURIComponent(fixtureId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId }),
    });
    if (!response.ok) throw new Error(`demo trigger failed: ${response.status}`);
    await refresh();
  }, [endpoint, refresh]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  return { snapshot, events, fixtures, loading, error, refresh, action, triggerFixture };
}

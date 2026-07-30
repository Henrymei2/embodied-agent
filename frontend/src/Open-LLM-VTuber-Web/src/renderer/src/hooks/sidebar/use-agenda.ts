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
  updated_at: string;
}

export function useAgenda() {
  const { baseUrl } = useWebSocket();
  const [snapshot, setSnapshot] = useState<AgendaSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const endpoint = `${baseUrl.replace(/\/$/, '')}/api/agenda`;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const response = await fetch(endpoint, { cache: 'no-store' });
      if (!response.ok) throw new Error(`agenda request failed: ${response.status}`);
      setSnapshot(await response.json() as AgendaSnapshot);
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

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  return { snapshot, loading, error, refresh, action };
}

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toaster } from '@/components/ui/toaster';
import { useWebSocket } from '@/context/websocket-context';

export type ScheduledEventKind = 'timer' | 'recurring' | 'one_time';

export interface ScheduledEvent {
  id: string;
  kind: ScheduledEventKind;
  label: string;
  prompt: string;
  priority: number;
  interval_seconds: number | null;
  report: boolean;
  next_run: string;
  created_at: string | null;
  run_count: number;
}

const REFRESH_INTERVAL_MS = 15_000;

export const useScheduledEvents = () => {
  const { t } = useTranslation();
  const { baseUrl } = useWebSocket();
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<ScheduledEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());

  const endpoint = `${baseUrl.replace(/\/$/, '')}/api/scheduler/events`;

  const fetchEvents = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const response = await fetch(endpoint, { cache: 'no-store' });
      if (!response.ok) throw new Error(`scheduler request failed: ${response.status}`);
      const payload = await response.json() as { events?: ScheduledEvent[] };
      if (!Array.isArray(payload.events)) throw new Error('invalid scheduler response');
      setEvents(payload.events);
      setError(false);
    } catch (requestError) {
      console.error('Failed to fetch scheduled events:', requestError);
      setError(true);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    if (!open) return undefined;
    void fetchEvents(true);
    const interval = window.setInterval(() => void fetchEvents(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [fetchEvents, open]);

  const deleteEvent = useCallback(async (eventId: string) => {
    setDeletingIds((current) => new Set(current).add(eventId));
    try {
      const response = await fetch(`${endpoint}/${encodeURIComponent(eventId)}`, {
        method: 'DELETE',
      });
      if (!response.ok) throw new Error(`scheduler deletion failed: ${response.status}`);
      setEvents((current) => current.filter((event) => event.id !== eventId));
      toaster.create({
        title: t('scheduler.deleteSuccess'),
        type: 'success',
        duration: 2000,
      });
    } catch (requestError) {
      console.error('Failed to delete scheduled event:', requestError);
      toaster.create({
        title: t('scheduler.deleteError'),
        type: 'error',
        duration: 2500,
      });
      void fetchEvents();
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(eventId);
        return next;
      });
    }
  }, [endpoint, fetchEvents, t]);

  return {
    open,
    setOpen,
    events,
    loading,
    error,
    deletingIds,
    fetchEvents,
    deleteEvent,
  };
};

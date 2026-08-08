import { Badge, Box, Button, Spinner, Text } from '@chakra-ui/react';
import { FiPause, FiPlay, FiRefreshCw, FiSquare, FiTrash2, FiZap } from 'react-icons/fi';
import {
  DrawerRoot, DrawerTrigger, DrawerContent, DrawerHeader, DrawerTitle,
  DrawerBody, DrawerFooter, DrawerActionTrigger, DrawerBackdrop, DrawerCloseTrigger,
} from '@/components/ui/drawer';
import { AgendaEntry, useAgenda } from '@/hooks/sidebar/use-agenda';
import { sidebarStyles } from './sidebar-styles';

function Entry({ entry, action }: {
  entry: AgendaEntry;
  action: (path: string, method?: string) => Promise<void>;
}) {
  const terminal = ['completed', 'failed', 'abandoned'].includes(entry.status);
  return (
    <Box {...sidebarStyles.schedulerDrawer.eventItem}>
      <Box {...sidebarStyles.schedulerDrawer.eventHeader}>
        <Box minWidth={0}>
          <Text {...sidebarStyles.schedulerDrawer.eventTitle}>{entry.title}</Text>
          <Badge {...sidebarStyles.schedulerDrawer.kindBadge}>
            {entry.kind} · {entry.status}
          </Badge>
        </Box>
        {!terminal && entry.status !== 'user' && (
          <Box display="flex" gap={1}>
            {entry.status === 'suspended' && (
              <Button size="xs" aria-label="Resume" onClick={() => void action(`/entries/${entry.id}/resume`)}>
                <FiPlay />
              </Button>
            )}
            {['queued', 'proposed'].includes(entry.status) ? (
              <Button size="xs" aria-label="Remove" onClick={() => void action(`/entries/${entry.id}`, 'DELETE')}>
                <FiTrash2 />
              </Button>
            ) : (
              <Button size="xs" aria-label="Stop" onClick={() => void action(`/entries/${entry.id}/abandon`)}>
                <FiSquare />
              </Button>
            )}
          </Box>
        )}
      </Box>
      {entry.progress_summary && <Text {...sidebarStyles.schedulerDrawer.prompt}>{entry.progress_summary}</Text>}
    </Box>
  );
}

export default function AgendaDrawer({ children }: { children: React.ReactNode }) {
  const { snapshot, events, fixtures, loading, error, refresh, action, triggerFixture } = useAgenda();
  const entries = snapshot?.entries || [];
  const capabilityCalls = Object.values(snapshot?.health.quota.capability_calls || {})
    .reduce((total, calls) => total + calls, 0);
  return (
    <DrawerRoot placement="start">
      <DrawerBackdrop />
      <DrawerTrigger asChild>{children}</DrawerTrigger>
      <DrawerContent style={sidebarStyles.historyDrawer.drawer.content}>
        <DrawerHeader>
          <DrawerTitle style={sidebarStyles.historyDrawer.drawer.title}>Global agenda</DrawerTitle>
          <DrawerCloseTrigger style={sidebarStyles.historyDrawer.drawer.closeButton} />
        </DrawerHeader>
        <DrawerBody>
          <Box display="flex" gap={2} marginBottom={3}>
            <Button onClick={() => void action(snapshot?.paused ? '/resume' : '/pause')}>
              {snapshot?.paused ? <FiPlay /> : <FiPause />}
              {snapshot?.paused ? 'Resume chain' : 'Pause chain'}
            </Button>
            <Button onClick={() => void refresh()}><FiRefreshCw />Refresh</Button>
          </Box>
          {snapshot && (
            <Box {...sidebarStyles.schedulerDrawer.eventItem} marginBottom={3}>
              <Box display="flex" gap={2} alignItems="center" flexWrap="wrap">
                <Badge {...sidebarStyles.schedulerDrawer.kindBadge}>
                  {snapshot.runtime.mode} runtime
                </Badge>
                <Text {...sidebarStyles.schedulerDrawer.prompt}>
                  {snapshot.runtime.running ? 'running' : 'stopped'}
                  {' · '}
                  revision {snapshot.revision}
                  {' · '}
                  fence {snapshot.runtime.fencing_token ?? snapshot.fencing_token}
                </Text>
              </Box>
              <Text {...sidebarStyles.schedulerDrawer.prompt}>
                Lease {snapshot.runtime.lease_status}
                {' · '}
                pending events {snapshot.health.events.pending}
                {' · '}
                dead letters {snapshot.health.dead_letters}
              </Text>
              <Text {...sidebarStyles.schedulerDrawer.prompt}>
                Completion{' '}
                {snapshot.health.completion_rate === null
                  ? '—'
                  : `${Math.round(snapshot.health.completion_rate * 100)}%`}
                {' · '}
                pending reports {snapshot.health.reports.pending}
                {' · '}
                planner failures {snapshot.health.planner.consecutive_failures}
              </Text>
              <Text {...sidebarStyles.schedulerDrawer.prompt}>
                Window usage: {snapshot.health.quota.planner_calls} planner
                {' · '}
                {capabilityCalls} capability
              </Text>
              {snapshot.health.planner.circuit_open_until && (
                <Text {...sidebarStyles.schedulerDrawer.prompt}>
                  Planner cooldown until{' '}
                  {new Date(snapshot.health.planner.circuit_open_until).toLocaleTimeString()}
                </Text>
              )}
              {(snapshot.runtime.last_error || snapshot.health.planner.last_error) && (
                <Text {...sidebarStyles.schedulerDrawer.prompt}>
                  {snapshot.runtime.last_error || snapshot.health.planner.last_error}
                </Text>
              )}
              {Object.entries(snapshot.event_producers).map(([name, health]) => (
                <Text key={name} {...sidebarStyles.schedulerDrawer.prompt}>
                  {name.replace('_', ' ')} producer: {health.status}
                  {health.consecutive_failures > 0 ? ` · ${health.consecutive_failures} failure(s)` : ''}
                </Text>
              ))}
            </Box>
          )}
          <Box {...sidebarStyles.schedulerDrawer.eventItem} marginBottom={3}>
            <Text {...sidebarStyles.schedulerDrawer.eventTitle}>Simulated demo triggers</Text>
            <Text {...sidebarStyles.schedulerDrawer.prompt} marginBottom={2}>
              These publish the production event shape and are always labeled simulated.
            </Text>
            <Box display="flex" gap={2} flexWrap="wrap">
              {fixtures.map((fixture) => (
                <Button key={fixture.id} size="xs" onClick={() => void triggerFixture(fixture.id)}>
                  <FiZap />{fixture.label}
                </Button>
              ))}
            </Box>
          </Box>
          {events.length > 0 && (
            <Box marginBottom={3}>
              <Text {...sidebarStyles.schedulerDrawer.eventTitle} marginBottom={2}>Recent external events</Text>
              {events.map((event) => (
                <Box key={event.id} {...sidebarStyles.schedulerDrawer.eventItem} marginBottom={2}>
                  <Box display="flex" gap={2} alignItems="center" flexWrap="wrap">
                    <Badge {...sidebarStyles.schedulerDrawer.kindBadge}>{event.kind}</Badge>
                    {event.trigger_reference?.simulated && <Badge colorPalette="orange">Simulated</Badge>}
                    <Badge {...sidebarStyles.schedulerDrawer.kindBadge}>{event.status}</Badge>
                  </Box>
                  <Text {...sidebarStyles.schedulerDrawer.prompt}>{event.summary}</Text>
                </Box>
              ))}
            </Box>
          )}
          {loading && !snapshot && <Spinner />}
          {error && <Text>Agenda could not be loaded.</Text>}
          {!loading && !error && entries.length === 0 && <Text>No agenda entries yet.</Text>}
          {entries.map((entry) => <Entry key={entry.id} entry={entry} action={action} />)}
        </DrawerBody>
        <DrawerFooter>
          <DrawerActionTrigger asChild><Button>Close</Button></DrawerActionTrigger>
        </DrawerFooter>
      </DrawerContent>
    </DrawerRoot>
  );
}

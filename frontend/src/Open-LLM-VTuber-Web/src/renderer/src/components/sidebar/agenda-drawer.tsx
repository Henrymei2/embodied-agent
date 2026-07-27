import { Badge, Box, Button, Spinner, Text } from '@chakra-ui/react';
import { FiPause, FiPlay, FiRefreshCw, FiSquare, FiTrash2 } from 'react-icons/fi';
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
  const { snapshot, loading, error, refresh, action } = useAgenda();
  const entries = snapshot?.entries || [];
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

import { Badge, Box, Button, Spinner, Text } from '@chakra-ui/react';
import { format, formatDistanceToNow } from 'date-fns';
import { TFunction } from 'i18next';
import { FiRefreshCw, FiTrash2 } from 'react-icons/fi';
import { useTranslation } from 'react-i18next';
import {
  DrawerRoot,
  DrawerTrigger,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
  DrawerBody,
  DrawerFooter,
  DrawerActionTrigger,
  DrawerBackdrop,
  DrawerCloseTrigger,
} from '@/components/ui/drawer';
import {
  ScheduledEvent,
  useScheduledEvents,
} from '@/hooks/sidebar/use-scheduled-events';
import { sidebarStyles } from './sidebar-styles';

interface ScheduledEventsDrawerProps {
  children: React.ReactNode;
}

function intervalText(seconds: number, t: TFunction): string {
  if (seconds % 86400 === 0) return t('scheduler.everyDays', { count: seconds / 86400 });
  if (seconds % 3600 === 0) return t('scheduler.everyHours', { count: seconds / 3600 });
  if (seconds % 60 === 0) return t('scheduler.everyMinutes', { count: seconds / 60 });
  return t('scheduler.everySeconds', { count: seconds });
}

function EventItem({
  event,
  deleting,
  onDelete,
}: {
  event: ScheduledEvent;
  deleting: boolean;
  onDelete: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  const nextRun = new Date(event.next_run);
  const validDate = !Number.isNaN(nextRun.getTime());
  const title = event.label || event.prompt || t('scheduler.untitled');
  const detail = event.label && event.prompt ? event.prompt : '';

  return (
    <Box {...sidebarStyles.schedulerDrawer.eventItem}>
      <Box {...sidebarStyles.schedulerDrawer.eventHeader}>
        <Box minWidth={0}>
          <Text {...sidebarStyles.schedulerDrawer.eventTitle}>{title}</Text>
          <Badge {...sidebarStyles.schedulerDrawer.kindBadge}>
            {t(`scheduler.kind.${event.kind}`)}
          </Badge>
        </Box>
        <Button
          aria-label={t('scheduler.delete')}
          title={t('scheduler.delete')}
          disabled={deleting}
          onClick={onDelete}
          {...sidebarStyles.schedulerDrawer.deleteButton}
        >
          {deleting ? <Spinner size="xs" /> : <FiTrash2 />}
        </Button>
      </Box>
      {detail && <Text {...sidebarStyles.schedulerDrawer.prompt}>{detail}</Text>}
      <Box {...sidebarStyles.schedulerDrawer.metadata}>
        <Text>
          {validDate
            ? `${format(nextRun, 'PP p')} (${formatDistanceToNow(nextRun, { addSuffix: true })})`
            : t('scheduler.unknownTime')}
        </Text>
        <Text>
          {event.interval_seconds
            ? intervalText(event.interval_seconds, t)
            : t('scheduler.oneTime')}
          {' · '}
          {event.report ? t('scheduler.reports') : t('scheduler.silent')}
        </Text>
      </Box>
    </Box>
  );
}

export default function ScheduledEventsDrawer({
  children,
}: ScheduledEventsDrawerProps): JSX.Element {
  const { t } = useTranslation();
  const {
    open,
    setOpen,
    events,
    loading,
    error,
    deletingIds,
    fetchEvents,
    deleteEvent,
  } = useScheduledEvents();

  return (
    <DrawerRoot open={open} onOpenChange={(e) => setOpen(e.open)} placement="start">
      <DrawerBackdrop />
      <DrawerTrigger asChild>{children}</DrawerTrigger>
      <DrawerContent style={sidebarStyles.historyDrawer.drawer.content}>
        <DrawerHeader>
          <DrawerTitle style={sidebarStyles.historyDrawer.drawer.title}>
            {t('scheduler.title')}
          </DrawerTitle>
          <DrawerCloseTrigger style={sidebarStyles.historyDrawer.drawer.closeButton} />
        </DrawerHeader>
        <DrawerBody>
          <Box {...sidebarStyles.schedulerDrawer.listContainer}>
            {loading && (
              <Box {...sidebarStyles.schedulerDrawer.state}>
                <Spinner />
                <Text>{t('scheduler.loading')}</Text>
              </Box>
            )}
            {!loading && error && (
              <Box {...sidebarStyles.schedulerDrawer.state}>
                <Text>{t('scheduler.loadError')}</Text>
                <Button onClick={() => void fetchEvents(true)}>
                  <FiRefreshCw />
                  {t('common.retry')}
                </Button>
              </Box>
            )}
            {!loading && !error && events.length === 0 && (
              <Box {...sidebarStyles.schedulerDrawer.state}>
                <Text>{t('scheduler.empty')}</Text>
              </Box>
            )}
            {!loading && !error && events.map((event) => (
              <EventItem
                key={event.id}
                event={event}
                deleting={deletingIds.has(event.id)}
                onDelete={() => void deleteEvent(event.id)}
              />
            ))}
          </Box>
        </DrawerBody>
        <DrawerFooter>
          <DrawerActionTrigger asChild>
            <Button {...sidebarStyles.historyDrawer.drawer.actionButton}>
              {t('common.close')}
            </Button>
          </DrawerActionTrigger>
        </DrawerFooter>
      </DrawerContent>
    </DrawerRoot>
  );
}

/* eslint-disable react/require-default-props */
import {
  Box, Button, Flex, HStack, IconButton, Menu, Text,
} from '@chakra-ui/react';
import {
  FiSettings, FiClock, FiPlus, FiChevronLeft, FiUsers, FiLayers, FiCalendar, FiZap,
} from 'react-icons/fi';
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { sidebarStyles } from './sidebar-styles';
import SettingUI from './setting/setting-ui';
import ChatHistoryPanel from './chat-history-panel';
import BottomTab from './bottom-tab';
import HistoryDrawer from './history-drawer';
import { useSidebar } from '@/hooks/sidebar/use-sidebar';
import GroupDrawer from './group-drawer';
import ScheduledEventsDrawer from './scheduled-events-drawer';
import { ModeType } from '@/context/mode-context';

// Type definitions
interface SidebarProps {
  isCollapsed?: boolean
  onToggle: () => void
}

interface HeaderButtonsProps {
  onSettingsOpen: () => void
  onNewHistory: () => void
  setMode: (mode: ModeType) => void
  currentMode: 'window' | 'pet'
  isElectron: boolean
}

// Reusable components
const ToggleButton = memo(({ isCollapsed, onToggle }: {
  isCollapsed: boolean
  onToggle: () => void
}) => {
  const { t } = useTranslation();
  return (
    <IconButton
      {...sidebarStyles.sidebar.toggleButton}
      aria-label={isCollapsed ? t('sidebar.expand') : t('sidebar.collapse')}
      title={isCollapsed ? t('sidebar.expand') : t('sidebar.collapse')}
      onClick={onToggle}
    >
      <Box
        transition="transform 0.28s ease"
        transform={isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)'}
      >
        <FiChevronLeft />
      </Box>
    </IconButton>
  );
});

ToggleButton.displayName = 'ToggleButton';

const ModeMenu = memo(({ setMode, currentMode, isElectron }: {
  setMode: (mode: ModeType) => void
  currentMode: ModeType
  isElectron: boolean
}) => {
  const { t } = useTranslation();
  return (
    <Menu.Root>
      <Menu.Trigger
        as={IconButton}
        {...sidebarStyles.sidebar.utilityButton}
        aria-label={t('sidebar.changeMode')}
        title={t('sidebar.changeMode')}
      >
        <FiLayers />
      </Menu.Trigger>
      <Menu.Positioner>
        <Menu.Content {...sidebarStyles.sidebar.modeMenu}>
          <Menu.RadioItemGroup value={currentMode}>
            <Menu.RadioItem value="window" onClick={() => setMode('window')}>
              <Menu.ItemIndicator />
              {t('sidebar.liveMode')}
            </Menu.RadioItem>
            <Menu.RadioItem
              value="pet"
              onClick={() => {
                if (isElectron) {
                  setMode('pet');
                }
              }}
              disabled={!isElectron}
              title={!isElectron ? t('sidebar.petModeDesktopOnly') : undefined}
            >
              <Menu.ItemIndicator />
              {t('sidebar.petMode')}
            </Menu.RadioItem>
          </Menu.RadioItemGroup>
        </Menu.Content>
      </Menu.Positioner>
    </Menu.Root>
  );
});

ModeMenu.displayName = 'ModeMenu';

const HeaderButtons = memo(({ onSettingsOpen, onNewHistory, setMode, currentMode, isElectron }: HeaderButtonsProps) => {
  const { t } = useTranslation();
  return (
    <HStack width="100%" gap="2">
      <Button
        {...sidebarStyles.sidebar.newConversationButton}
        onClick={onNewHistory}
        aria-label={t('sidebar.newConversation')}
      >
        <FiPlus />
        {t('sidebar.new')}
      </Button>
      <GroupDrawer>
        <IconButton
          {...sidebarStyles.sidebar.utilityButton}
          aria-label={t('group.management')}
          title={t('group.management')}
        >
          <FiUsers />
        </IconButton>
      </GroupDrawer>
      <HistoryDrawer>
        <IconButton
          {...sidebarStyles.sidebar.utilityButton}
          aria-label={t('history.chatHistoryList')}
          title={t('history.chatHistoryList')}
        >
          <FiClock />
        </IconButton>
      </HistoryDrawer>
      <ScheduledEventsDrawer>
        <IconButton
          {...sidebarStyles.sidebar.utilityButton}
          aria-label={t('scheduler.title')}
          title={t('scheduler.title')}
        >
          <FiCalendar />
        </IconButton>
      </ScheduledEventsDrawer>
      <ModeMenu setMode={setMode} currentMode={currentMode} isElectron={isElectron} />
      <IconButton
        {...sidebarStyles.sidebar.utilityButton}
        onClick={onSettingsOpen}
        aria-label={t('common.settings')}
        title={t('common.settings')}
      >
        <FiSettings />
      </IconButton>
    </HStack>
  );
});

HeaderButtons.displayName = 'HeaderButtons';

const SidebarContent = memo(({ 
  onSettingsOpen, 
  onNewHistory, 
  setMode, 
  currentMode,
  isElectron
}: HeaderButtonsProps) => {
  const { t } = useTranslation();
  return (
    <Box {...sidebarStyles.sidebar.content}>
      <Flex {...sidebarStyles.sidebar.brandRow}>
        <Flex {...sidebarStyles.sidebar.brandMark}>
          <FiZap size={17} />
        </Flex>
        <Box minWidth={0}>
          <Text {...sidebarStyles.sidebar.brandName}>SAIL</Text>
          <Text {...sidebarStyles.sidebar.brandTagline}>
            {t('sidebar.productTagline')}
          </Text>
        </Box>
      </Flex>
      <Box {...sidebarStyles.sidebar.header}>
        <HeaderButtons
          onSettingsOpen={onSettingsOpen}
          onNewHistory={onNewHistory}
          setMode={setMode}
          currentMode={currentMode}
          isElectron={isElectron}
        />
      </Box>
      <ChatHistoryPanel />
      <BottomTab />
    </Box>
  );
});

SidebarContent.displayName = 'SidebarContent';

// Main component
function Sidebar({ isCollapsed = false, onToggle }: SidebarProps): JSX.Element {
  const {
    settingsOpen,
    onSettingsOpen,
    onSettingsClose,
    createNewHistory,
    setMode,
    currentMode,
    isElectron,
  } = useSidebar();

  return (
    <Box {...sidebarStyles.sidebar.container(isCollapsed)}>
      {isCollapsed && (
        <Flex {...sidebarStyles.sidebar.collapsedBrand} aria-hidden="true">
          <FiZap size={16} />
        </Flex>
      )}
      <ToggleButton isCollapsed={isCollapsed} onToggle={onToggle} />

      {!isCollapsed && !settingsOpen && (
        <SidebarContent
          onSettingsOpen={onSettingsOpen}
          onNewHistory={createNewHistory}
          setMode={setMode}
          currentMode={currentMode}
          isElectron={isElectron}
        />
      )}

      {!isCollapsed && settingsOpen && (
        <SettingUI
          open={settingsOpen}
          onClose={onSettingsClose}
          onToggle={onToggle}
        />
      )}
    </Box>
  );
}

export default Sidebar;

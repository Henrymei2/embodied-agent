/* eslint-disable react/require-default-props */
import {
  Box, Textarea, IconButton,
} from '@chakra-ui/react';
import { BsMicFill, BsMicMuteFill } from 'react-icons/bs';
import { IoHandRightSharp } from 'react-icons/io5';
import { FiArrowUp, FiChevronDown } from 'react-icons/fi';
import { memo } from 'react';
import { useTranslation } from 'react-i18next';
import { footerStyles } from './footer-styles';
import AIStateIndicator from './ai-state-indicator';
import { useFooter } from '@/hooks/footer/use-footer';
import { status } from '@/styles/tokens';

// Type definitions
interface FooterProps {
  isCollapsed?: boolean
  onToggle?: () => void
}

interface ToggleButtonProps {
  isCollapsed: boolean
  onToggle?: () => void
}

interface ActionButtonsProps {
  micOn: boolean
  onMicToggle: () => void
  onInterrupt: () => void
}

interface MessageInputProps {
  value: string
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void
  onSend: () => void
  onCompositionStart: () => void
  onCompositionEnd: () => void
}

// Reusable components
const ToggleButton = memo(({ isCollapsed, onToggle }: ToggleButtonProps) => {
  const { t } = useTranslation();
  return (
    <IconButton
      {...footerStyles.footer.toggleButton}
      onClick={onToggle}
      aria-label={isCollapsed ? t('footer.expandComposer') : t('footer.collapseComposer')}
      title={isCollapsed ? t('footer.expandComposer') : t('footer.collapseComposer')}
    >
      <Box
        transition="transform 0.28s ease"
        transform={isCollapsed ? 'rotate(180deg)' : 'rotate(0deg)'}
      >
        <FiChevronDown />
      </Box>
    </IconButton>
  );
});

ToggleButton.displayName = 'ToggleButton';

const ActionButtons = memo(({ micOn, onMicToggle, onInterrupt }: ActionButtonsProps) => {
  const { t } = useTranslation();
  return (
    <>
      <IconButton
        {...footerStyles.footer.actionButton}
        color={micOn ? status.successFg : status.dangerFg}
        bg={micOn ? status.successBg : status.dangerBg}
        borderColor={micOn ? status.successBorder : status.dangerBorder}
        aria-label={micOn ? t('footer.muteMicrophone') : t('footer.unmuteMicrophone')}
        title={micOn ? t('footer.muteMicrophone') : t('footer.unmuteMicrophone')}
        onClick={onMicToggle}
      >
        {micOn ? <BsMicFill /> : <BsMicMuteFill />}
      </IconButton>
      <IconButton
        {...footerStyles.footer.actionButton}
        display={{ base: 'none', sm: 'inline-flex' }}
        aria-label={t('footer.nudge')}
        title={t('footer.nudge')}
        color={status.warningFg}
        bg={status.warningBg}
        borderColor={status.warningBorder}
        onClick={onInterrupt}
      >
        <IoHandRightSharp size="19" />
      </IconButton>
    </>
  );
});

ActionButtons.displayName = 'ActionButtons';

const MessageInput = memo(({
  value,
  onChange,
  onKeyDown,
  onSend,
  onCompositionStart,
  onCompositionEnd,
}: MessageInputProps) => {
  const { t } = useTranslation();

  return (
    <Box position="relative" flex={1} minWidth={0}>
      <Textarea
        value={value}
        onChange={onChange}
        onKeyDown={onKeyDown}
        onCompositionStart={onCompositionStart}
        onCompositionEnd={onCompositionEnd}
        placeholder={t('footer.typeYourMessage')}
        aria-label={t('footer.typeYourMessage')}
        {...footerStyles.footer.input}
      />
      <IconButton
        {...footerStyles.footer.sendButton}
        aria-label={t('footer.send')}
        title={t('footer.send')}
        disabled={!value.trim()}
        onClick={onSend}
      >
        <FiArrowUp size={18} />
      </IconButton>
    </Box>
  );
});

MessageInput.displayName = 'MessageInput';

// Main component
function Footer({ isCollapsed = false, onToggle }: FooterProps): JSX.Element {
  const {
    inputValue,
    handleInputChange,
    handleKeyPress,
    handleSend,
    handleCompositionStart,
    handleCompositionEnd,
    handleInterrupt,
    handleMicToggle,
    micOn,
  } = useFooter();

  return (
    <Box {...footerStyles.footer.container(isCollapsed)}>
      <ToggleButton isCollapsed={isCollapsed} onToggle={onToggle} />

      <Box {...footerStyles.footer.composerWrap}>
        <Box {...footerStyles.footer.composer}>
          <Box {...footerStyles.footer.statePosition}>
            <AIStateIndicator />
          </Box>
          <ActionButtons
            micOn={micOn}
            onMicToggle={handleMicToggle}
            onInterrupt={handleInterrupt}
          />
          <MessageInput
            value={inputValue}
            onChange={handleInputChange}
            onKeyDown={handleKeyPress}
            onSend={handleSend}
            onCompositionStart={handleCompositionStart}
            onCompositionEnd={handleCompositionEnd}
          />
        </Box>
      </Box>
    </Box>
  );
}

export default Footer;

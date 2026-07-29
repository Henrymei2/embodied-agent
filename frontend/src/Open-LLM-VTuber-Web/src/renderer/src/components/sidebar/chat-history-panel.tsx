/* eslint-disable function-paren-newline */
/* eslint-disable react/jsx-one-expression-per-line */
/* eslint-disable no-trailing-spaces */
/* eslint-disable no-nested-ternary */
/* eslint-disable react/require-default-props */
import { Box, Spinner, Flex, Text, Icon } from '@chakra-ui/react';
import { sidebarStyles, chatPanelStyles } from './sidebar-styles';
import { MainContainer, ChatContainer, MessageList as ChatMessageList, Message as ChatMessage, Avatar as ChatAvatar } from '@chatscope/chat-ui-kit-react';
import '@chatscope/chat-ui-kit-styles/dist/default/styles.min.css';
import { useChatHistory } from '@/context/chat-history-context';
import { Global } from '@emotion/react';
import { useConfig } from '@/context/character-config-context';
import { useWebSocket } from '@/context/websocket-context';
import { FaTools, FaCheck, FaTimes } from 'react-icons/fa';
import { FiMessageCircle } from 'react-icons/fi';
import { useTranslation } from 'react-i18next';
import { accent, border, radius, text, typography } from '@/styles/tokens';

const MAX_TOOL_CONTENT_LENGTH = 360;

function getToolStatusLabel(status?: string): string {
  switch (status) {
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'error':
      return 'Error';
    default:
      return 'Tool call';
  }
}

function getToolContentLabel(status?: string): string {
  if (status === 'running') {
    return 'Input';
  }
  if (status === 'error') {
    return 'Error';
  }
  return 'Result';
}

function formatToolContent(content?: string): string {
  if (!content) {
    return '';
  }

  const trimmed = content.trim();
  if (!trimmed) {
    return '';
  }

  const compact = trimmed.replace(/^Input:\s*/i, '').replace(/\s+/g, ' ');
  if (compact.length <= MAX_TOOL_CONTENT_LENGTH) {
    return compact;
  }
  return `${compact.slice(0, MAX_TOOL_CONTENT_LENGTH)}...`;
}

// Main component
function ChatHistoryPanel(): JSX.Element {
  const { t } = useTranslation();
  const { messages } = useChatHistory(); // Get messages directly from context
  const { confName } = useConfig();
  const { baseUrl } = useWebSocket();
  const userName = "Me";

  const validMessages = messages.filter((msg) => msg.content || // Keep messages with content
     (msg.type === 'tool_call_status' && msg.status === 'running') || // Keep running tools
     (msg.type === 'tool_call_status' && msg.status === 'completed') || // Keep completed tools
     (msg.type === 'tool_call_status' && msg.status === 'error'), // Keep error tools
  );

  return (
    <Box
      {...sidebarStyles.chatHistoryPanel.container}
    >
      <Global styles={chatPanelStyles} />
      <Flex alignItems="center" justifyContent="space-between" px="2" pb="2">
        <Text {...sidebarStyles.chatHistoryPanel.title} mb="0">
          {t('sidebar.conversation')}
        </Text>
        {validMessages.length > 0 && (
          <Text
            {...typography.caption}
            color={text.quaternary}
            fontVariantNumeric="tabular-nums"
          >
            {t('sidebar.messageCount', { count: validMessages.length })}
          </Text>
        )}
      </Flex>
      <Box
        flex="1"
        minHeight="0"
        borderRadius={radius.xl}
        border={`1px solid ${border.subtle}`}
        overflow="hidden"
      >
        <MainContainer>
          <ChatContainer>
            <ChatMessageList>
              {validMessages.length === 0 ? (
                <Flex
                  height="100%"
                  px="6"
                  alignItems="center"
                  justifyContent="center"
                  flexDirection="column"
                  textAlign="center"
                >
                  <Flex
                    width="48px"
                    height="48px"
                    mb="4"
                    alignItems="center"
                    justifyContent="center"
                    borderRadius={radius.xl}
                    color={accent[300]}
                    bg={accent.soft}
                    border={`1px solid ${accent.ring}`}
                  >
                    <FiMessageCircle size={21} />
                  </Flex>
                  <Text {...typography.bodyStrong} color={text.primary}>
                    {t('sidebar.emptyTitle')}
                  </Text>
                  <Text
                    {...typography.footnote}
                    mt="1.5"
                    maxWidth="220px"
                    color={text.tertiary}
                    textWrap="balance"
                  >
                    {t('sidebar.noMessages')}
                  </Text>
                </Flex>
              ) : (
                validMessages.map((msg) => {
                // Check if it's a tool call message
                if (msg.type === 'tool_call_status') {
                  const statusLabel = getToolStatusLabel(msg.status);
                  const contentLabel = getToolContentLabel(msg.status);
                  const toolContent = formatToolContent(msg.content);
                  const speakerName = msg.name || confName || 'AI';

                  return (
                    // Render Tool Call Indicator using msg properties
                    <Flex
                      key={msg.id} // Use tool_id as key
                      {...sidebarStyles.toolCallIndicator.container}
                    >
                      <Flex {...sidebarStyles.toolCallIndicator.header}>
                        <Icon
                          as={FaTools}
                          {...sidebarStyles.toolCallIndicator.icon}
                        />
                        <Text {...sidebarStyles.toolCallIndicator.title}>
                          {speakerName} | {statusLabel} | {msg.tool_name}
                        </Text>
                        {msg.status === "running" && (
                          <Spinner
                            size="xs"
                            color={sidebarStyles.toolCallIndicator.spinner.color}
                            ml={sidebarStyles.toolCallIndicator.spinner.ml}
                          />
                        )}
                        {msg.status === "completed" && (
                          <Icon
                            as={FaCheck}
                            {...sidebarStyles.toolCallIndicator.completedIcon}
                          />
                        )}
                        {msg.status === "error" && (
                          <Icon
                            as={FaTimes}
                            {...sidebarStyles.toolCallIndicator.errorIcon}
                          />
                        )}
                      </Flex>
                      {toolContent && (
                        <Text {...sidebarStyles.toolCallIndicator.detail}>
                          {contentLabel}: {toolContent}
                        </Text>
                      )}
                    </Flex>
                  );
                } 
                // Render Standard Chat Message (human or ai text)
                return (
                  <ChatMessage
                    key={msg.id}
                    model={{
                      message: msg.content,
                      sentTime: msg.timestamp,
                      sender: msg.role === 'ai'
                        ? (msg.name || confName || 'AI')
                        : userName,
                      direction: msg.role === 'ai' ? 'incoming' : 'outgoing',
                      position: 'single',
                    }}
                    avatarPosition={msg.role === 'ai' ? 'tl' : 'tr'}
                    avatarSpacer={false}
                  >
                    <ChatAvatar>
                      {msg.role === 'ai' ? (
                        msg.avatar ? (
                          <img
                            src={`${baseUrl}/avatars/${msg.avatar}`}
                            alt="avatar"
                            style={{ width: '100%', height: '100%', borderRadius: '50%' }}
                            onError={(e) => {
                              const target = e.target as HTMLImageElement;
                              const fallbackName = msg.name || confName || 'A';
                              target.outerHTML = `<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; border-radius: 50%; background-color: var(--chakra-colors-blue-500); color: white; font-size: 14px;">${fallbackName[0].toUpperCase()}</div>`;
                            }}
                          />
                        ) : (
                          (msg.name && msg.name[0].toUpperCase()) ||
                            (confName && confName[0].toUpperCase()) ||
                            'A'
                        )
                      ) : (
                        userName[0].toUpperCase()
                      )}
                    </ChatAvatar>
                  </ChatMessage>
                );
                })
              )}
            </ChatMessageList>
          </ChatContainer>
        </MainContainer>
      </Box>
    </Box>
  );
}

export default ChatHistoryPanel;

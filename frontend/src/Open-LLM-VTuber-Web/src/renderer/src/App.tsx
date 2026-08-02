/* eslint-disable no-shadow */
// import { StrictMode } from 'react';
import { Box, Flex, ChakraProvider, defaultSystem } from "@chakra-ui/react";
import { useState, useEffect } from "react";
// import Canvas from './components/canvas/canvas'; // Likely unused now
import Sidebar from "./components/sidebar/sidebar";
import Footer from "./components/footer/footer";
import { AiStateProvider } from "./context/ai-state-context";
import { SubtitleProvider } from "./context/subtitle-context";
import { BgUrlProvider } from "./context/bgurl-context";
import { layoutStyles } from "./layout";
import WebSocketHandler from "./services/websocket-handler";
import { CameraProvider } from "./context/camera-context";
import { ChatHistoryProvider } from "./context/chat-history-context";
import { CharacterConfigProvider } from "./context/character-config-context";
import { Toaster } from "./components/ui/toaster";
import { VADProvider } from "./context/vad-context";
import TitleBar from "./components/electron/title-bar";
import { InputSubtitle } from "./components/electron/input-subtitle";
import { ProactiveSpeakProvider } from "./context/proactive-speak-context";
import { ScreenCaptureProvider } from "./context/screen-capture-context";
import { GroupProvider } from "./context/group-context";
import { BrowserProvider } from "./context/browser-context";
import "@chatscope/chat-ui-kit-styles/dist/default/styles.min.css";
import Background from "./components/canvas/background";
import WebSocketStatus from "./components/canvas/ws-status";
import Subtitle from "./components/canvas/subtitle";
import { ModeProvider, useMode } from "./context/mode-context";
import DynamicStage from "./components/canvas/dynamic-stage";
import { DynamicStageProvider } from "./context/dynamic-stage-context";
import { PresenceProvider } from "./context/presence-context";
import PresenceIndicator from "./components/canvas/presence-indicator";
import WorkspaceHeader from "./components/shell/workspace-header";
import {
  StudyConditionProvider,
  useStudyCondition,
} from "./context/study-condition-context";
import { AgentActivityProvider } from "./context/agent-activity-context";
import {
  RendererModeProvider,
  useRendererMode,
} from "./context/renderer-mode-context";
import { VrStageProvider } from "./context/vr-stage-context";
import VrStage from "./components/vr/vr-stage";

function RendererEntry(): JSX.Element {
  const { mode, error, enterVr, useNormal } = useRendererMode();

  if (mode === "vr") return <VrStage />;
  if (mode === "normal") return <AppContent />;

  return (
    <Flex
      position="fixed"
      inset="0"
      align="center"
      justify="center"
      direction="column"
      gap="4"
      bg="#101827"
      color="white"
      textAlign="center"
      padding="6"
    >
      <Box fontSize="2xl" fontWeight="semibold">Embodied Agent</Box>
      {mode === "detecting" && <Box>Checking immersive VR support…</Box>}
      {mode === "vr-starting" && <Box>Starting VR…</Box>}
      {mode === "choosing" && (
        <>
          <button type="button" onClick={() => void enterVr()}>Enter VR</button>
          <button type="button" onClick={useNormal}>Use normal mode</button>
        </>
      )}
      {error && <Box color="red.200">{error}</Box>}
    </Flex>
  );
}

function AppContent(): JSX.Element {
  const [showSidebar, setShowSidebar] = useState(
    () => window.innerWidth >= 1080,
  );
  const [isFooterCollapsed, setIsFooterCollapsed] = useState(false);
  const { mode } = useMode();
  const isElectron = window.api !== undefined;
  // In the embodiment ablation there is no room: the same companion, the same
  // tools, without a place. Everything else on screen is unchanged.
  const { condition } = useStudyCondition();

  useEffect(() => {
    const handleResize = () => {
      const vh = window.innerHeight * 0.01;
      document.documentElement.style.setProperty("--vh", `${vh}px`);
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const scenePetStyle = {
    position: "absolute" as const,
    overflow: "hidden",
    transition: "all 0.3s ease-in-out",
    pointerEvents: "auto" as const,
    top: 0,
    left: 0,
    width: "100vw",
    height: "100vh",
    zIndex: 15,
  };

  return (
    <>
      {condition.embodiment && mode === "pet" && (
        <Box {...scenePetStyle}>
          <DynamicStage />
        </Box>
      )}

      {/* Conditional Rendering of Window UI */}
      {mode === "window" && (
        <>
          {isElectron && <TitleBar />}
          {/* Apply styles by spreading */}
          <Flex {...layoutStyles.appContainer}>
            <Box
              {...layoutStyles.sidebar}
              width={{
                base: showSidebar ? "min(88vw, 372px)" : "56px",
                md: showSidebar ? "372px" : "56px",
              }}
            >
              <Sidebar
                isCollapsed={!showSidebar}
                onToggle={() => setShowSidebar(!showSidebar)}
              />
            </Box>
            <Box {...layoutStyles.mainContent}>
              <Background />
              <WorkspaceHeader
                sidebarCollapsed={!showSidebar}
                roomEnabled={condition.embodiment}
                connectionStatus={<WebSocketStatus />}
                presenceControl={<PresenceIndicator />}
              />
              <Box {...layoutStyles.stageArea}>
                {condition.embodiment && (
                  <Box {...layoutStyles.stageFrame}>
                    <DynamicStage />
                    <Box
                      position="absolute"
                      inset="0"
                      pointerEvents="none"
                      bg="linear-gradient(180deg, rgba(4, 6, 12, 0.12) 0%, transparent 18%, transparent 74%, rgba(4, 6, 12, 0.2) 100%)"
                      zIndex={7}
                    />
                  </Box>
                )}
              </Box>
              <Box
                position="absolute"
                bottom={isFooterCollapsed ? "42px" : "122px"}
                left="50%"
                transform="translateX(-50%)"
                zIndex={15}
                width={{ base: "calc(100% - 32px)", md: "min(720px, 72%)" }}
              >
                <Subtitle />
              </Box>
              <Box
                {...layoutStyles.footer}
                zIndex={10}
                {...(isFooterCollapsed && layoutStyles.collapsedFooter)}
              >
                <Footer
                  isCollapsed={isFooterCollapsed}
                  onToggle={() => setIsFooterCollapsed(!isFooterCollapsed)}
                />
              </Box>
            </Box>
          </Flex>
        </>
      )}

      {/* Conditional Rendering of Pet Mode UI */}
      {mode === "pet" && <InputSubtitle />}
    </>
  );
}

function App(): JSX.Element {
  return (
    <ChakraProvider value={defaultSystem}>
      <RendererModeProvider>
        {/* ModeProvider needs to wrap AppContent to provide mode to getGlobalStyles */}
        <ModeProvider>
          <AppWithGlobalStyles />
        </ModeProvider>
      </RendererModeProvider>
    </ChakraProvider>
  );
}

// New component to access mode for global styles
function AppWithGlobalStyles(): JSX.Element {
  return (
    <>
      <CameraProvider>
        <ScreenCaptureProvider>
          <CharacterConfigProvider>
            <ChatHistoryProvider>
              <AiStateProvider>
                <ProactiveSpeakProvider>
                  <SubtitleProvider>
                    <VADProvider>
                      <BgUrlProvider>
                        <GroupProvider>
                          <BrowserProvider>
                            <DynamicStageProvider>
                              <VrStageProvider>
                                <PresenceProvider>
                                  <StudyConditionProvider>
                                    <AgentActivityProvider>
                                      <WebSocketHandler>
                                        <Toaster />
                                        <RendererEntry />
                                      </WebSocketHandler>
                                    </AgentActivityProvider>
                                  </StudyConditionProvider>
                                </PresenceProvider>
                              </VrStageProvider>
                            </DynamicStageProvider>
                          </BrowserProvider>
                        </GroupProvider>
                      </BgUrlProvider>
                    </VADProvider>
                  </SubtitleProvider>
                </ProactiveSpeakProvider>
              </AiStateProvider>
            </ChatHistoryProvider>
          </CharacterConfigProvider>
        </ScreenCaptureProvider>
      </CameraProvider>
    </>
  );
}

export default App;

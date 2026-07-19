interface Window {
  api?: {
    setIgnoreMouseEvents: (ignore: boolean) => void
    showContextMenu?: () => void
    onModeChanged: (callback: (mode: string) => void) => void
    openGoogleAuthorization?: (url: string) => Promise<boolean>
    updateComponentHover?: (componentId: string, isHovering: boolean) => void
  }
}

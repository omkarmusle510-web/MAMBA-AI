export interface MambaDesktopAPI {
  isDesktop: boolean;
  platform: string;
  version: string;
  getLifecycleState?: () => string;
  onLifecycleState?: (callback: (state: string) => void) => () => void;
  reportActivity?: (type: string) => void;
  reportTaskState?: (active: boolean) => void;
  reportAwaitingPermission?: (awaiting: boolean) => void;
  reportVoiceState?: (active: boolean) => void;
  reportState?: (state: string) => void;
  onState?: (callback: (state: string) => void) => () => void;
  activate?: () => void;
  toggleMainWindow?: () => void;
}

declare global {
  interface Window {
    mambaDesktop?: MambaDesktopAPI;
  }
}

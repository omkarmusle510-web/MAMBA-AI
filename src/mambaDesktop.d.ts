export interface MambaDesktopAPI {
  isDesktop: boolean;
  platform: string;
  version: string;
  getLifecycleState?: () => string;
  getAutoStart?: () => boolean;
  setAutoStart?: (enabled: boolean) => boolean;
  notifyWakeDetected?: () => void;
  onVoiceTurnRequest?: (callback: () => void) => () => void;
  consumePendingVoiceTurn?: () => boolean;
  notifyWakeSettingChanged?: (enabled: boolean) => void;
  onWakeSetting?: (callback: (enabled: boolean) => void) => () => void;
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

export interface MambaDesktopAPI {
  isDesktop: boolean;
  platform: string;
  version: string;
  getLifecycleState?: () => string;
  getAutoStart?: () => boolean;
  setAutoStart?: (enabled: boolean) => boolean;
  notifyWakeDetected?: () => void;
  /** TEMP DIAG: forward wake diagnostics to the main-process terminal. */
  reportWakeDiag?: (message: string) => void;
  /** Wake-word KWS service (main process): init the spotter, returns {ok, error?}. */
  wakeKwsInit?: (opts: { threshold?: number }) => Promise<{ ok: boolean; error?: string }>;
  /** Stream one 16 kHz mono float32 PCM chunk to the main-process spotter. */
  wakeKwsAudioChunk?: (samples: Float32Array) => void;
  /** Release the main-process decode stream. */
  wakeKwsStop?: () => void;
  /** Fired when the main-process spotter detects the wake keyword. */
  onWakeKwsDetected?: (callback: (keyword: string) => void) => () => void;
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

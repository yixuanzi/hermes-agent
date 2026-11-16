export const FRONTEND_SETTINGS_STORAGE_KEY = 'aegis_frontend_settings';

export interface FrontendSettings {
  chatAutoOpenHtmlOnTaskComplete: boolean;
}

export const DEFAULT_FRONTEND_SETTINGS: Readonly<FrontendSettings> = {
  chatAutoOpenHtmlOnTaskComplete: true,
};

function parseFrontendSettings(raw: string | null): FrontendSettings {
  if (!raw) {
    return { ...DEFAULT_FRONTEND_SETTINGS };
  }

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return { ...DEFAULT_FRONTEND_SETTINGS };
    }
    const value = (parsed as Partial<FrontendSettings>).chatAutoOpenHtmlOnTaskComplete;
    return {
      chatAutoOpenHtmlOnTaskComplete: value !== false,
    };
  } catch {
    return { ...DEFAULT_FRONTEND_SETTINGS };
  }
}

export function getFrontendSettings(storage: Storage = window.localStorage): FrontendSettings {
  return parseFrontendSettings(storage.getItem(FRONTEND_SETTINGS_STORAGE_KEY));
}

export function saveFrontendSettings(
  settings: FrontendSettings,
  storage: Storage = window.localStorage,
): FrontendSettings {
  const nextSettings: FrontendSettings = {
    chatAutoOpenHtmlOnTaskComplete: settings.chatAutoOpenHtmlOnTaskComplete !== false,
  };
  storage.setItem(FRONTEND_SETTINGS_STORAGE_KEY, JSON.stringify(nextSettings));
  return nextSettings;
}

export function setChatAutoOpenHtmlOnTaskComplete(
  enabled: boolean,
  storage: Storage = window.localStorage,
): FrontendSettings {
  return saveFrontendSettings({ chatAutoOpenHtmlOnTaskComplete: enabled }, storage);
}

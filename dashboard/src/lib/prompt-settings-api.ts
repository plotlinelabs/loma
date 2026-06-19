const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export type PromptSettingKey = "identity_guidelines" | "company_information";

export interface PromptSetting {
  setting_key: PromptSettingKey;
  title: string;
  content: string;
  default_content: string;
  updated_at?: string | null;
  updated_by?: string | null;
}

export async function fetchPromptSettings(): Promise<PromptSetting[]> {
  const res = await fetch(`${API_BASE}/api/prompt-settings`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to fetch prompt settings: ${res.status}`);
  }
  const data = await res.json();
  return data.settings;
}

export async function updatePromptSetting(
  settingKey: PromptSettingKey,
  content: string,
): Promise<PromptSetting> {
  const res = await fetch(`${API_BASE}/api/prompt-settings/${settingKey}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error || `Failed to update prompt setting: ${res.status}`);
  }
  const data = await res.json();
  return data.setting;
}

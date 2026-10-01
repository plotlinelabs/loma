/**
 * Loma Devices client — enroll Device Runners and manage the emulators /
 * simulators they expose to the agent.
 */

import { basePath } from "@/lib/api";

export interface DeviceRunner {
  runner_id: string;
  name: string;
  owner: string;
  is_owner: boolean;
  hostname: string | null;
  os: string | null;
  version: string | null;
  capabilities: string[];
  shared_with: string[];
  online: boolean;
  last_seen: string | null;
  created_at: string | null;
  templates?: { name: string; platform: "android" | "ios"; clean: boolean }[];
  latest_version?: string;
  update_available?: boolean;
  self_update?: boolean;
}

export interface DeviceRecord {
  device_id: string;
  platform: "android" | "ios" | "unknown";
  name: string;
  os_version: string;
  virtual: boolean;
  runner: string;
  owner: string;
  online: boolean;
  leased_by: { owner: string; scope: string; expires_at: string } | null;
}

export interface Enrollment {
  token: string;
  expires_at: string;
  server: string;
  commands: string[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${basePath}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed: ${res.status}`);
  return data as T;
}

export function fetchDevices(): Promise<{ runners: DeviceRunner[]; devices: DeviceRecord[] }> {
  return request("/api/devices");
}

export function createEnrollment(name: string): Promise<Enrollment> {
  return request("/api/devices/enrollments", { method: "POST", body: JSON.stringify({ name }) });
}

export function updateRunner(
  runnerId: string,
  update: { name?: string; shared_with?: string[] },
): Promise<DeviceRunner> {
  return request(`/api/devices/runners/${runnerId}`, { method: "PATCH", body: JSON.stringify(update) });
}

export function revokeRunner(runnerId: string): Promise<{ revoked: boolean }> {
  return request(`/api/devices/runners/${runnerId}`, { method: "DELETE" });
}

export function releaseDevice(deviceId: string): Promise<{ released: boolean }> {
  return request("/api/devices/release", { method: "POST", body: JSON.stringify({ device_id: deviceId }) });
}

export interface BuildSettings {
  repos: string[];
  workflows: string[];
  env_repos: string[];
  env_workflows: string[];
  can_edit: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export function fetchBuildSettings(): Promise<BuildSettings> {
  return request("/api/devices/build-settings");
}

export function saveBuildSettings(update: { repos: string[]; workflows: string[] }): Promise<BuildSettings> {
  return request("/api/devices/build-settings", { method: "PUT", body: JSON.stringify(update) });
}

export interface DeviceActivityEvent {
  at: string;
  device_id: string;
  actor: string;
  scope: string;
  op: string;
  ok: boolean;
  error?: string;
  duration_ms?: number;
  detail?: Record<string, unknown>;
}

export interface DeviceSession {
  scope: string;
  actor: string;
  started_at: string;
  ended_at: string;
  ops: number;
  failures: number;
  conversation_id?: string;
}

export function fetchDeviceActivity(
  deviceId: string,
): Promise<{ device_id: string; sessions: DeviceSession[]; events: DeviceActivityEvent[] }> {
  return request(`/api/devices/activity?device_id=${encodeURIComponent(deviceId)}`);
}

/** One live-view frame as an object URL (revoke it when replaced). */
/** One live-view frame, plus who holds the device ("you", "other" or "none") per the server. */
export async function fetchDeviceScreen(
  deviceId: string,
  signal?: AbortSignal,
): Promise<{ url: string; held: "you" | "other" | "none" }> {
  const res = await fetch(`${basePath}/api/devices/screen?device_id=${encodeURIComponent(deviceId)}`, { signal });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw Object.assign(new Error(data.error || `Request failed: ${res.status}`), { status: res.status });
  }
  const held = res.headers.get("X-Device-Held");
  return {
    url: URL.createObjectURL(await res.blob()),
    held: held === "you" || held === "other" ? held : "none",
  };
}

/** Hand the device back while the page is closing: keepalive lets the request outlive the page. */
export function handBackOnUnload(deviceId: string): void {
  fetch(`${basePath}/api/devices/takeover`, {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: deviceId, action: "end" }),
  }).catch(() => undefined);
}

export function takeover(
  deviceId: string,
  action: "start" | "end",
): Promise<{ held: boolean; until?: string; paused_session?: string | null }> {
  return request("/api/devices/takeover", { method: "POST", body: JSON.stringify({ device_id: deviceId, action }) });
}

export function takeoverInput(deviceId: string, op: string, args: Record<string, unknown>): Promise<unknown> {
  return request("/api/devices/takeover", {
    method: "POST",
    body: JSON.stringify({ device_id: deviceId, action: "input", op, args }),
  });
}

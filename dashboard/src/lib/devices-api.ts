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
  latest_version?: string;
  update_available?: boolean;
  capabilities: string[];
  shared_with: string[];
  online: boolean;
  last_seen: string | null;
  created_at: string | null;
  templates?: { name: string; platform: "android" | "ios"; clean: boolean }[];
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
  /** ok | recovering (the runner is restarting it) | down (crashed or closed) | offline (runner offline) */
  state?: "ok" | "recovering" | "down" | "offline";
  error?: string;
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

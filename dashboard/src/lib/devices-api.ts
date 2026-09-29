/**
 * Loma Devices client — enroll Device Runners and manage the emulators /
 * simulators they expose to the agent.
 */

const API_BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

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
  const res = await fetch(`${API_BASE}${path}`, {
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

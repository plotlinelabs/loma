"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  RiAddLine,
  RiAndroidLine,
  RiAppleLine,
  RiCloseLine,
  RiComputerLine,
  RiDeleteBinLine,
  RiLockUnlockLine,
  RiRefreshLine,
  RiShareLine,
  RiSmartphoneLine,
} from "@remixicon/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/EmptyState";
import ClientTimestamp from "@/components/ClientTimestamp";
import { CopyButton } from "@/components/CopyButton";
import DeviceBuildSources from "@/components/DeviceBuildSources";
import {
  DeviceRecord,
  DeviceRunner,
  Enrollment,
  createEnrollment,
  fetchDevices,
  releaseDevice,
  revokeRunner,
  updateRunner,
} from "@/lib/devices-api";

function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full shrink-0 ${online ? "bg-green-500" : "bg-muted-foreground/40"}`}
      aria-hidden="true"
    />
  );
}

function DeviceRow({ device, canRelease, busy, onRelease }: {
  device: DeviceRecord;
  canRelease: boolean;
  busy: boolean;
  onRelease: (id: string) => void;
}) {
  const Icon = device.platform === "ios" ? RiAppleLine : device.platform === "android" ? RiAndroidLine : RiSmartphoneLine;
  return (
    <div className="flex items-center gap-2 rounded-md border px-3 py-2">
      <Icon size={15} className="text-muted-foreground shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-[13px] text-foreground truncate">
          {device.name} <span className="text-muted-foreground">· {device.platform} {device.os_version}</span>
          {!device.virtual && <span className="ml-1 text-amber-600 text-xs">physical</span>}
          {device.state === "recovering" && <span className="ml-1 text-amber-600 text-xs">restarting…</span>}
          {device.state === "down" && (
            <span className="ml-1 text-red-600 text-xs" title={device.error || "Crashed or closed"}>
              down
            </span>
          )}
        </div>
        <div className="text-xs text-muted-foreground truncate">
          <code>{device.device_id}</code>
          {device.leased_by ? (
            <>
              {" · in use by "}
              {device.leased_by.owner}
              {" until "}
              <ClientTimestamp iso={device.leased_by.expires_at} variant="short" />
            </>
          ) : (
            " · free"
          )}
        </div>
      </div>
      {device.leased_by && canRelease && (
        <Button
          variant="ghost"
          size="sm"
          className="h-8 px-2 text-xs shrink-0"
          disabled={busy}
          onClick={() => onRelease(device.device_id)}
        >
          <RiLockUnlockLine size={14} />
          Release
        </Button>
      )}
    </div>
  );
}

/**
 * Loma Devices: enroll a Device Runner on your own machine so the agent can
 * install PR builds on your emulators/simulators and test them end to end.
 * Rendered as the "Devices" tab on the Integrations page.
 */
export default function DevicesPanel() {
  const [runners, setRunners] = useState<DeviceRunner[] | null>(null);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [name, setName] = useState("");
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [busy, setBusy] = useState(false);
  const [enrollError, setEnrollError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [sharing, setSharing] = useState<{ id: string; value: string } | null>(null);
  // Only the newest request may update state, so a slow poll never overwrites fresher data.
  const sequence = useRef(0);

  const load = useCallback(async () => {
    const mine = ++sequence.current;
    try {
      const data = await fetchDevices();
      if (mine !== sequence.current) return;
      setRunners(data.runners);
      setDevices(data.devices);
      setPollError(null);
    } catch (e) {
      if (mine !== sequence.current) return;
      setPollError(e instanceof Error ? e.message : "Failed to load devices");
      setRunners((prev) => prev ?? []); // keep the last good list on a transient failure
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const run = async (fn: () => Promise<unknown>, setErr: (e: string | null) => void = setActionError) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const onEnroll = () =>
    run(async () => {
      setEnrollment(await createEnrollment(name.trim() || "My machine"));
      setName("");
    }, setEnrollError);

  const onRevoke = (runner: DeviceRunner) => {
    if (!window.confirm(`Revoke "${runner.name}"? The agent loses access to its devices immediately.`)) return;
    run(() => revokeRunner(runner.runner_id));
  };

  const onSaveSharing = (runnerId: string, value: string) =>
    run(async () => {
      const emails = value.split(/[\s,]+/).map((e) => e.trim()).filter(Boolean);
      await updateRunner(runnerId, { shared_with: emails });
      setSharing(null);
    });

  return (
    <div className="space-y-2">
      <p className="text-[13px] text-muted-foreground">
        Let the agent test mobile builds on your own Android emulators and iOS simulators. Run the
        Loma Device Runner on your machine: it connects out to Loma (no tunnel or open port), and
        devices show up here whenever the machine is on.
      </p>

      <Card>
        <CardContent className="space-y-3">
          <div className="text-[13px] font-semibold text-foreground">Add a machine</div>
          <div className="text-xs text-muted-foreground">
            To update a machine that is already listed, run{" "}
            <code className="bg-muted rounded px-1">python3 loma_device_runner.py setup</code> on it; no new token is
            needed. Running setup with a new token on the same machine also keeps its existing entry.
          </div>
          <div className="flex items-center gap-2">
            <Input
              value={name}
              placeholder="Machine name (e.g. Work MacBook)"
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && !busy && onEnroll()}
            />
            <Button size="sm" onClick={onEnroll} disabled={busy} className="shrink-0">
              <RiAddLine size={16} />
              Get setup commands
            </Button>
          </div>
          {enrollment && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 space-y-2">
              <div className="flex items-start gap-2">
                <div className="text-xs font-medium text-foreground flex-1">
                  Run these in a terminal on the machine. The token works once and expires{" "}
                  <ClientTimestamp iso={enrollment.expires_at} variant="short" />.
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0 shrink-0"
                  aria-label="Hide setup commands"
                  onClick={() => setEnrollment(null)}
                >
                  <RiCloseLine size={14} />
                </Button>
              </div>
              {enrollment.commands.map((command) => (
                <div key={command} className="flex items-center gap-2">
                  <code className="text-xs bg-muted rounded px-2 py-1.5 flex-1 overflow-x-auto whitespace-nowrap">
                    {command}
                  </code>
                  <CopyButton text={command} />
                </div>
              ))}
              <div className="text-xs text-muted-foreground">
                Setup installs the runner in <code className="bg-muted rounded px-1">~/.loma-device-runner</code>{" "}
                and keeps it running in the background, also after a restart. Then boot an emulator or simulator.
              </div>
            </div>
          )}
          {enrollError && <div className="text-xs text-red-500">{enrollError}</div>}
        </CardContent>
      </Card>

      <DeviceBuildSources />

      <div className="flex items-center justify-between pt-1">
        <div className="text-[13px] font-semibold text-foreground">Machines</div>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => load()}>
          <RiRefreshLine size={14} />
          Refresh
        </Button>
      </div>
      {(actionError || pollError) && (
        <div className="text-xs text-red-500" role="alert">
          {actionError || `Could not refresh: ${pollError}`}
        </div>
      )}

      {runners === null ? (
        <>
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </>
      ) : runners.length === 0 ? (
        <EmptyState
          icon={RiComputerLine}
          title="No machines yet"
          description="Add a machine above, then boot an emulator or simulator on it."
        />
      ) : (
        runners.map((runner) => {
          const mine = devices.filter((d) => d.device_id.startsWith(`${runner.runner_id}/`));
          return (
            <Card key={runner.runner_id}>
              <CardContent className="space-y-2">
                <div className="flex items-center gap-2">
                  <StatusDot online={runner.online} />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-semibold text-foreground truncate">
                      {runner.name}
                      {!runner.is_owner && (
                        <span className="ml-2 text-xs font-normal text-muted-foreground">shared by {runner.owner}</span>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground truncate">
                      {runner.online ? "Online" : "Offline"}
                      {runner.last_seen && !runner.online && (
                        <>
                          {" · last seen "}
                          <ClientTimestamp iso={runner.last_seen} variant="short" />
                        </>
                      )}
                      {runner.hostname ? ` · ${runner.hostname}` : ""}
                      {runner.version ? ` · runner ${runner.version}` : ""}
                      {runner.capabilities.length ? ` · ${runner.capabilities.join(", ")}` : ""}
                    </div>
                    {runner.update_available && (
                      <div className="text-xs text-amber-600">
                        Update to runner {runner.latest_version}: download loma_device_runner.py again and run{" "}
                        <code>python3 loma_device_runner.py setup</code> on that machine (no new token needed)
                      </div>
                    )}
                  </div>
                  {runner.is_owner && (
                    <>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 w-8 p-0 text-muted-foreground shrink-0"
                        aria-label="Share"
                        disabled={busy}
                        onClick={() => setSharing({ id: runner.runner_id, value: runner.shared_with.join(", ") })}
                      >
                        <RiShareLine size={15} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 w-8 p-0 text-muted-foreground hover:text-red-500 shrink-0"
                        aria-label="Revoke machine"
                        disabled={busy}
                        onClick={() => onRevoke(runner)}
                      >
                        <RiDeleteBinLine size={15} />
                      </Button>
                    </>
                  )}
                </div>

                {sharing?.id === runner.runner_id && (
                  <div className="flex items-center gap-2">
                    <Input
                      value={sharing.value}
                      aria-label="Share with (comma-separated emails)"
                      placeholder="Share with (comma-separated emails)"
                      onChange={(e) => setSharing({ id: runner.runner_id, value: e.target.value })}
                      onKeyDown={(e) =>
                        e.key === "Enter" && !e.nativeEvent.isComposing && !busy && onSaveSharing(runner.runner_id, sharing.value)
                      }
                    />
                    <Button size="sm" disabled={busy} onClick={() => onSaveSharing(runner.runner_id, sharing.value)}>
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setSharing(null)}>
                      Cancel
                    </Button>
                  </div>
                )}
                {runner.shared_with.length > 0 && sharing?.id !== runner.runner_id && (
                  <div className="text-xs text-muted-foreground">Shared with {runner.shared_with.join(", ")}</div>
                )}

                {mine.length === 0 ? (
                  <div className="text-xs text-muted-foreground">
                    {runner.online
                      ? "No emulators or simulators running. Boot one and it will appear within ~15s."
                      : "Start the runner on this machine to see its devices."}
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {mine.map((device) => (
                      <DeviceRow
                        key={device.device_id}
                        device={device}
                        canRelease={runner.is_owner}
                        busy={busy}
                        onRelease={(id) => run(() => releaseDevice(id))}
                      />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })
      )}
    </div>
  );
}

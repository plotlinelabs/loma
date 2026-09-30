"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { BuildSettings, fetchBuildSettings, saveBuildSettings } from "@/lib/devices-api";

const split = (value: string) => value.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean);

/**
 * Admin-only allowlists for installing GitHub CI builds on devices: which repos' artifacts the agent
 * may install, and which workflow files it may start. Empty means nothing is allowed.
 */
export default function DeviceBuildSources() {
  const [settings, setSettings] = useState<BuildSettings | null>(null);
  const [repos, setRepos] = useState("");
  const [workflows, setWorkflows] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const apply = (data: BuildSettings) => {
    setSettings(data);
    setRepos(data.repos.join(", "));
    setWorkflows(data.workflows.join(", "));
  };

  useEffect(() => {
    fetchBuildSettings()
      .then(apply)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load build sources"));
  }, []);

  const onSave = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      apply(await saveBuildSettings({ repos: split(repos), workflows: split(workflows) }));
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const fromEnv = (items: string[]) =>
    items.length > 0 && (
      <div className="text-xs text-muted-foreground">Also allowed by the server config: {items.join(", ")}</div>
    );

  return (
    <Card>
      <CardContent className="space-y-3">
        <div>
          <div className="text-[13px] font-semibold text-foreground">Build sources</div>
          <div className="text-xs text-muted-foreground">
            Only needed to install builds straight from GitHub CI. The agent can only install artifacts from these
            repos and only start these workflow files. Uploaded APK / .app files don&apos;t need this.
            {settings && !settings.can_edit && " Only an admin can change these."}
          </div>
        </div>
        {settings === null && !error ? (
          <Skeleton className="h-16 w-full" />
        ) : settings ? (
          <>
            <div className="space-y-1">
              <label htmlFor="build-repos" className="text-xs font-medium text-foreground">
                Repos
              </label>
              <Input
                id="build-repos"
                value={repos}
                disabled={!settings.can_edit || busy}
                placeholder="owner/app-repo, owner/other-repo"
                onChange={(e) => setRepos(e.target.value)}
              />
              {fromEnv(settings.env_repos)}
            </div>
            <div className="space-y-1">
              <label htmlFor="build-workflows" className="text-xs font-medium text-foreground">
                Workflows the agent may start
              </label>
              <Input
                id="build-workflows"
                value={workflows}
                disabled={!settings.can_edit || busy}
                placeholder="build-android.yml, build-ios.yml"
                onChange={(e) => setWorkflows(e.target.value)}
              />
              {fromEnv(settings.env_workflows)}
            </div>
            {settings.can_edit && (
              <div className="flex items-center gap-3">
                <Button size="sm" onClick={onSave} disabled={busy}>
                  Save
                </Button>
                {saved && <span className="text-xs text-muted-foreground">Saved. Takes effect within a minute.</span>}
              </div>
            )}
          </>
        ) : null}
        {error && (
          <div className="text-xs text-red-500" role="alert">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

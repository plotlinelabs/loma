"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { basePath, fetchSkills } from "../../lib/api";
import type { Skill } from "../../lib/api";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { RiBookOpenLine } from "@remixicon/react";
import { EmptyState } from "@/components/EmptyState";

function chatUrl(prompt: string): string {
  return `${basePath}/chat?prompt=${encodeURIComponent(prompt)}`;
}

function buildCreateSkillPrompt(): string {
  return [
    "I want to create a new Loma skill.",
    "",
    "Please help me design the skill first, then create it using `python3 tools/loma_skills.py create` only after I confirm the slug, description, and SKILL.md content.",
    "",
    "Ask me for:",
    "- skill slug",
    "- when the agent should use it",
    "- required steps or playbook content",
    "- supporting files or assets, if any",
  ].join("\n");
}

function formatUpdated(value?: string): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const createPromptUrl = useMemo(() => chatUrl(buildCreateSkillPrompt()), []);

  useEffect(() => {
    setLoading(true);
    fetchSkills()
      .then((data) => setSkills(data.skills))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load skills"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-3 animate-fade-in-up">
      <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-3">
        <div>
          <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Skills</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Company playbooks and supporting files the agent can search, read, and update through chat.
          </p>
        </div>
        <Button asChild>
          <Link href={createPromptUrl}>
            Create Skill in Chat
          </Link>
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden overflow-x-auto">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow className="border-b border-border bg-muted/50">
              <TableHead className="w-[130px]">Skill</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="w-[100px]">Tags</TableHead>
              <TableHead className="w-[70px]">Files</TableHead>
              <TableHead className="w-[90px]">Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <>
                {[1, 2, 3].map((i) => (
                  <TableRow key={i}>
                    <TableCell><Skeleton className="h-4 w-32" /><Skeleton className="h-3 w-20 mt-1.5" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                    <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                    <TableCell><Skeleton className="h-4 w-28" /></TableCell>
                  </TableRow>
                ))}
              </>
            ) : skills.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5}>
                  <EmptyState icon={RiBookOpenLine} title="No skills yet" description="Skills can be created from the chat interface" />
                </TableCell>
              </TableRow>
            ) : (
              skills.map((skill) => {
                const slug = skill.slug || skill.name;
                const files = skill.file_details || [];
                const assetCount = files.filter((file) => file.kind === "local_asset").length;
                const textCount = files.filter((file) => file.kind === "inline_text").length;
                return (
                  <TableRow key={slug}>
                    <TableCell className="align-top overflow-hidden">
                      <Link href={`/skills/${slug}`} className="text-sm font-semibold text-foreground hover:text-brand-700 truncate block">
                        {skill.name || slug}
                      </Link>
                      <div className="text-xs text-muted-foreground font-mono mt-0.5 truncate">{slug}</div>
                    </TableCell>
                    <TableCell className="align-top text-sm text-muted-foreground overflow-hidden"><span className="line-clamp-2">{skill.description || "-"}</span></TableCell>
                    <TableCell className="align-top overflow-hidden">
                      {skill.tags?.length ? (
                        <div className="flex flex-wrap gap-1 overflow-hidden max-h-[3rem]">
                          {skill.tags.map((tag) => (
                            <Badge key={tag} variant="secondary" className="text-[11px]">
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      ) : <span className="text-xs text-muted-foreground">-</span>}
                    </TableCell>
                    <TableCell className="align-top text-xs text-muted-foreground whitespace-nowrap">
                      {textCount} text · {assetCount} asset{assetCount === 1 ? "" : "s"}
                    </TableCell>
                    <TableCell className="align-top text-xs text-muted-foreground whitespace-nowrap">{formatUpdated(skill.updated_at)}</TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useSession } from "next-auth/react";
import { fetchCurrentUser } from "./governance-api";
import type { User, SystemRole } from "./governance-api";
import { pinConversation, unpinConversation, fetchProjects, createProject, deleteConversation, updateConversation, assignConversationToProject, removeConversationFromProject } from "./api";
import type { Project } from "./api";

interface UserContextValue {
  user: User | null;
  loading: boolean;
  isAdmin: boolean;
  isOperator: boolean;
  isAnalyst: boolean;
  isChatter: boolean;
  /** Minimum role check — true if user's role is >= the given level */
  hasRole: (minRole: SystemRole) => boolean;
  /** Refresh user data from the API */
  refresh: () => void;
  /** Set of pinned conversation IDs */
  pinnedIds: Set<string>;
  /** Check if a conversation is pinned */
  isPinned: (conversationId: string) => boolean;
  /** Toggle pin state for a conversation */
  togglePin: (conversationId: string) => Promise<void>;
  /** Available projects */
  projects: Project[];
  /** Refresh projects list */
  refreshProjects: () => void;
  /** Create a new project */
  addProject: (name: string) => Promise<Project>;
  /** Rename a conversation */
  renameConversation: (conversationId: string, newTitle: string) => Promise<void>;
  /** Delete a conversation */
  removeConversation: (conversationId: string) => Promise<void>;
  /** Assign conversation to project */
  assignToProject: (conversationId: string, projectId: string) => Promise<void>;
  /** Remove conversation from project */
  unassignFromProject: (conversationId: string) => Promise<void>;
}

const ROLE_HIERARCHY: Record<SystemRole, number> = {
  admin: 5,
  maintainer: 4,
  operator: 3,
  analyst: 2,
  chatter: 1,
};

const UserContext = createContext<UserContextValue>({
  user: null,
  loading: true,
  isAdmin: false,
  isOperator: false,
  isAnalyst: false,
  isChatter: true,
  hasRole: () => false,
  refresh: () => {},
  pinnedIds: new Set(),
  isPinned: () => false,
  togglePin: async () => {},
  projects: [],
  refreshProjects: () => {},
  addProject: async () => ({} as Project),
  renameConversation: async () => {},
  removeConversation: async () => {},
  assignToProject: async () => {},
  unassignFromProject: async () => {},
});

export function UserProvider({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const loadUser = () => {
    if (status === "authenticated" && session?.user?.email) {
      // Normal flow: Google OAuth session exists
      setLoading(true);
      fetchCurrentUser()
        .then(setUser)
        .catch((e) => console.error("Failed to fetch current user:", e))
        .finally(() => setLoading(false));
    } else if (status === "unauthenticated") {
      // Preview deployments have no NextAuth session (middleware is removed).
      // Try fetching anyway — the Python backend uses a fallback identity in DEV mode.
      setLoading(true);
      fetchCurrentUser()
        .then(setUser)
        .catch(() => setUser(null))
        .finally(() => setLoading(false));
    }
  };

  useEffect(loadUser, [status, session?.user?.email]);

  const role = user?.system_role ?? "chatter";
  const roleLevel = ROLE_HIERARCHY[role] ?? 0;

  const pinnedIds = useMemo(
    () => new Set((user?.pinned_conversations ?? []).map((p) => p.conversation_id)),
    [user?.pinned_conversations],
  );

  const [projects, setProjects] = useState<Project[]>([]);

  const loadProjects = () => {
    fetchProjects()
      .then((data) => setProjects(data.projects))
      .catch((e) => console.error("Failed to fetch projects:", e));
  };

  useEffect(() => {
    if (!loading && user) {
      loadProjects();
    }
  }, [loading, user?.email]);

  const addProject = async (name: string): Promise<Project> => {
    const result = await createProject({ name });
    loadProjects();
    return result.project;
  };

  const renameConversation = async (conversationId: string, newTitle: string) => {
    await updateConversation(conversationId, { title: newTitle });
  };

  const removeConversation = async (conversationId: string) => {
    await deleteConversation(conversationId);
    // If it was pinned, refresh user to update pins
    if (pinnedIds.has(conversationId)) {
      loadUser();
    }
  };

  const assignToProject = async (conversationId: string, projectId: string) => {
    await assignConversationToProject(conversationId, projectId);
    loadProjects(); // refresh counts
  };

  const unassignFromProject = async (conversationId: string) => {
    await removeConversationFromProject(conversationId);
    loadProjects(); // refresh counts
  };

  const togglePin = async (conversationId: string) => {
    try {
      if (pinnedIds.has(conversationId)) {
        await unpinConversation(conversationId);
      } else {
        await pinConversation(conversationId);
      }
      loadUser();
    } catch (e) {
      console.error("Failed to toggle pin:", e);
      throw e;
    }
  };

  const value: UserContextValue = {
    user,
    loading,
    isAdmin: role === "admin",
    isOperator: roleLevel >= ROLE_HIERARCHY.operator,
    isAnalyst: roleLevel >= ROLE_HIERARCHY.analyst,
    isChatter: true,
    hasRole: (minRole: SystemRole) => roleLevel >= (ROLE_HIERARCHY[minRole] ?? 0),
    refresh: loadUser,
    pinnedIds,
    isPinned: (conversationId: string) => pinnedIds.has(conversationId),
    togglePin,
    projects,
    refreshProjects: loadProjects,
    addProject,
    renameConversation,
    removeConversation,
    assignToProject,
    unassignFromProject,
  };

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export const useUser = () => useContext(UserContext);

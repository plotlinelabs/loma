"use client";

import { useState, useEffect, useRef } from "react";

interface ChatContextMenuProps {
  conversationId: string;
  conversationTitle: string;
  isPinned: boolean;
  projectId?: string | null;
  projects: Array<{ project_id: string; name: string }>;
  onRename: (conversationId: string, newTitle: string) => Promise<void>;
  onDelete: (conversationId: string) => Promise<void>;
  onTogglePin: (conversationId: string) => Promise<void>;
  onAssignProject: (conversationId: string, projectId: string) => Promise<void>;
  onRemoveProject: (conversationId: string) => Promise<void>;
  onCreateProject: (name: string) => Promise<void>;
  triggerClassName?: string;
}

export default function ChatContextMenu({
  conversationId,
  conversationTitle,
  isPinned,
  projectId,
  projects,
  onRename,
  onDelete,
  onTogglePin,
  onAssignProject,
  onRemoveProject,
  onCreateProject,
  triggerClassName,
}: ChatContextMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [showRename, setShowRename] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [showProjectMenu, setShowProjectMenu] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [renameValue, setRenameValue] = useState(conversationTitle);
  const [newProjectName, setNewProjectName] = useState("");
  const [loading, setLoading] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        closeAll();
      }
    }
    if (isOpen || showDelete) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [isOpen, showDelete]);

  useEffect(() => {
    if (showRename && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [showRename]);

  useEffect(() => {
    if (showNewProject && projectInputRef.current) {
      projectInputRef.current.focus();
    }
  }, [showNewProject]);

  function closeAll() {
    setIsOpen(false);
    setShowRename(false);
    setShowDelete(false);
    setShowProjectMenu(false);
    setShowNewProject(false);
    setNewProjectName("");
  }

  async function handleRename() {
    const trimmed = renameValue.trim();
    if (!trimmed || trimmed === conversationTitle) {
      setShowRename(false);
      return;
    }
    setLoading(true);
    try {
      await onRename(conversationId, trimmed);
      closeAll();
    } catch (e) {
      console.error("Failed to rename:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    setLoading(true);
    try {
      await onDelete(conversationId);
      closeAll();
    } catch (e) {
      console.error("Failed to delete:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateProject() {
    const trimmed = newProjectName.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      await onCreateProject(trimmed);
      setShowNewProject(false);
      setNewProjectName("");
    } catch (e) {
      console.error("Failed to create project:", e);
    } finally {
      setLoading(false);
    }
  }

  // Rename inline dialog
  if (showRename) {
    return (
      <div ref={menuRef} className="relative inline-block">
        <div className="absolute right-0 top-full mt-1 z-50 bg-surface border border-gray-200 rounded-lg shadow-lg p-2 w-64 animate-fade-in">
          <input
            ref={renameInputRef}
            type="text"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRename();
              if (e.key === "Escape") closeAll();
            }}
            maxLength={200}
            disabled={loading}
            className="w-full px-2.5 py-1.5 text-sm text-gray-700 bg-gray-50 border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-accent-200 focus:border-accent-200"
            placeholder="Enter new title..."
          />
          <div className="flex items-center gap-1.5 mt-2">
            <button
              onClick={handleRename}
              disabled={loading || !renameValue.trim()}
              className="flex-1 px-2.5 py-1 text-xs font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-md transition-colors disabled:opacity-40"
            >
              {loading ? "Saving..." : "Save"}
            </button>
            <button
              onClick={closeAll}
              disabled={loading}
              className="flex-1 px-2.5 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-md transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Delete confirmation dialog
  if (showDelete) {
    return (
      <div ref={menuRef} className="relative inline-block">
        <div className="absolute right-0 top-full mt-1 z-50 bg-surface border border-gray-200 rounded-lg shadow-lg p-3 w-64 animate-fade-in">
          <p className="text-sm font-medium text-gray-900 mb-1">Delete conversation?</p>
          <p className="text-xs text-gray-500 mb-3">This cannot be undone.</p>
          <div className="flex items-center gap-1.5">
            <button
              onClick={handleDelete}
              disabled={loading}
              className="flex-1 px-2.5 py-1.5 text-xs font-medium text-white bg-red-600 hover:bg-red-700 rounded-md transition-colors disabled:opacity-40"
            >
              {loading ? "Deleting..." : "Delete"}
            </button>
            <button
              onClick={closeAll}
              disabled={loading}
              className="flex-1 px-2.5 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-md transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div ref={menuRef} className="relative inline-block">
      {/* Trigger button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          setIsOpen(!isOpen);
          setShowProjectMenu(false);
        }}
        className={triggerClassName || "p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"}
        title="More actions"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM12.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0ZM18.75 12a.75.75 0 1 1-1.5 0 .75.75 0 0 1 1.5 0Z" />
        </svg>
      </button>

      {/* Menu dropdown */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-surface border border-gray-200 rounded-lg shadow-lg py-1 w-48 animate-fade-in">
          {/* Pin / Unpin */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onTogglePin(conversationId);
              closeAll();
            }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-gray-700 hover:bg-gray-50 transition-colors text-left"
          >
            {isPinned ? (
              <>
                <svg className="w-4 h-4 text-amber-500" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2z" />
                </svg>
                Unpin
              </>
            ) : (
              <>
                <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 4h6v5l2.5 2.5H13v5.5l-1 2-1-2V11.5H6.5L9 9V4z" />
                </svg>
                Pin
              </>
            )}
          </button>

          {/* Rename */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setIsOpen(false);
              setRenameValue(conversationTitle);
              setShowRename(true);
            }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-gray-700 hover:bg-gray-50 transition-colors text-left"
          >
            <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125" />
            </svg>
            Rename
          </button>

          {/* Add to project */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setShowProjectMenu(!showProjectMenu);
            }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-gray-700 hover:bg-gray-50 transition-colors text-left"
          >
            <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 0 1 4.5 9.75h15A2.25 2.25 0 0 1 21.75 12v.75m-8.69-6.44-2.12-2.12a1.5 1.5 0 0 0-1.061-.44H4.5A2.25 2.25 0 0 0 2.25 6v12a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9a2.25 2.25 0 0 0-2.25-2.25h-5.379a1.5 1.5 0 0 1-1.06-.44Z" />
            </svg>
            {projectId ? "Move to project" : "Add to project"}
            <svg className="w-3 h-3 text-gray-400 ml-auto" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
            </svg>
          </button>

          {/* Project submenu */}
          {showProjectMenu && (
            <div className="border-t border-gray-100 mt-1 pt-1">
              {projectId && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onRemoveProject(conversationId);
                    closeAll();
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-red-600 hover:bg-red-50 transition-colors text-left"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                  </svg>
                  Remove from project
                </button>
              )}
              {projects.map((p) => (
                <button
                  key={p.project_id}
                  onClick={(e) => {
                    e.stopPropagation();
                    onAssignProject(conversationId, p.project_id);
                    closeAll();
                  }}
                  className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] hover:bg-gray-50 transition-colors text-left ${
                    p.project_id === projectId ? "text-brand-600 font-medium" : "text-gray-700"
                  }`}
                >
                  <span className="w-2 h-2 rounded-full bg-gray-300 flex-shrink-0" />
                  <span className="truncate">{p.name}</span>
                  {p.project_id === projectId && (
                    <svg className="w-3.5 h-3.5 text-brand-600 ml-auto flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                    </svg>
                  )}
                </button>
              ))}
              {showNewProject ? (
                <div className="px-3 py-1.5">
                  <input
                    ref={projectInputRef}
                    type="text"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleCreateProject();
                      if (e.key === "Escape") {
                        setShowNewProject(false);
                        setNewProjectName("");
                      }
                    }}
                    maxLength={100}
                    disabled={loading}
                    className="w-full px-2 py-1 text-xs text-gray-700 bg-gray-50 border border-gray-200 rounded-md focus:outline-none focus:ring-1 focus:ring-accent-200"
                    placeholder="Project name..."
                    onClick={(e) => e.stopPropagation()}
                  />
                </div>
              ) : (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowNewProject(true);
                  }}
                  className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-brand-600 hover:bg-brand-50 transition-colors text-left"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                  </svg>
                  New project
                </button>
              )}
            </div>
          )}

          {/* Divider */}
          <div className="border-t border-gray-100 my-1" />

          {/* Delete */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setIsOpen(false);
              setShowDelete(true);
            }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-red-600 hover:bg-red-50 transition-colors text-left"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
            </svg>
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

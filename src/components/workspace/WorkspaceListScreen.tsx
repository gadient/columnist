import React, { useState } from 'react';
import { Plus, Grid3X3, Layers, RefreshCw, Edit3, Trash2 } from 'lucide-react';
import { DeleteConfirmationModal } from '../common/DeleteConfirmationModal';
import { HowItWorks } from '../onboarding/HowItWorks';
import { HowChatWorks } from '../onboarding/HowChatWorks';

export const WorkspaceListScreen = ({ workspaces, onOpenWorkspace, onCreateWorkspace, onLoadDemo, onRenameWorkspace, onDeleteWorkspace, isDemoLoading }) => {
  const [newWsName, setNewWsName] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  // Guard the cascade: deleting a workspace hard-deletes all its boards, so confirm first
  // (parity with single-board deletion, which already uses this modal).
  const [pendingDelete, setPendingDelete] = useState(null);
  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const [showHowChatWorks, setShowHowChatWorks] = useState(false);

  const submitCreate = () => {
    const name = newWsName.trim();
    if (!name) return;
    onCreateWorkspace(name);
    setNewWsName('');
    setShowCreate(false);
  };

  const submitRename = (id) => {
    const name = renameValue.trim();
    if (name) onRenameWorkspace(id, name);
    setRenamingId(null);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-gray-900 dark:to-gray-950 p-6">
      {showHowItWorks && <HowItWorks onClose={() => setShowHowItWorks(false)} />}
      {showHowChatWorks && <HowChatWorks onClose={() => setShowHowChatWorks(false)} />}
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-4 mb-10">
          <div className="w-14 h-14 bg-blue-600 rounded-2xl flex items-center justify-center shadow-lg shrink-0">
            <Grid3X3 size={30} className="text-fg-inverted" />
          </div>
          <div>
            <h1 className="text-3xl font-bold text-fg">Columnist</h1>
            {/* Two features, two links. The notes importer is still the headline — it is the one
                that does something no other board does. The assistant is named second and marked
                alpha in the same breath, so nobody meets it expecting a finished thing. */}
            <p className="text-fg-muted">
              Kanban boards that build themselves from your meeting notes ·{' '}
              <button
                onClick={() => setShowHowItWorks(true)}
                className="font-medium text-accent underline-offset-2 hover:underline"
              >
                See how it works
              </button>
            </p>
            <p className="mt-0.5 text-fg-muted">
              …and an assistant that answers questions about them ·{' '}
              <button
                onClick={() => setShowHowChatWorks(true)}
                className="font-medium text-accent underline-offset-2 hover:underline"
              >
                See how the assistant works
              </button>{' '}
              <span className="ml-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
                Alpha
              </span>
            </p>
          </div>
        </div>

        {/* First run only. Someone with no workspaces has nowhere to discover the feature: the demo
            boards arrive full of cards, so the empty-board pitch never fires for them. This is the
            one screen guaranteed to be on their path, so the orientation belongs here. */}
        {workspaces.length === 0 && (
          <div className="mb-8 rounded-xl border border-indigo-200 dark:border-line bg-surface/70 p-5 shadow-sm">
            <h2 className="text-base font-semibold text-fg">New here? Start with the demo.</h2>
            <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-fg-muted">
              Load the demo workspace, open any board, and hit{' '}
              <span className="font-medium text-fg">📥 Import notes</span> — paste a meeting
              note and Columnist proposes cards with owners, due dates and priorities. You review
              every card before anything is created. A sample note is provided on demo boards.
            </p>
          </div>
        )}

        {/* Action bar */}
        <div className="flex flex-wrap gap-3 mb-8">
          <button
            onClick={() => setShowCreate(v => !v)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Plus size={16} /> Create Workspace
          </button>
          <button
            onClick={onLoadDemo}
            disabled={isDemoLoading}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-semibold rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {isDemoLoading ? <RefreshCw size={15} className="animate-spin" /> : <Layers size={15} />}
            {isDemoLoading ? 'Loading…' : 'Load Demo Workspace'}
          </button>
        </div>

        {/* Create workspace form */}
        {showCreate && (
          <div className="mb-6 bg-surface rounded-xl p-4 shadow-sm border border-line flex gap-3 items-center max-w-md">
            <input
              autoFocus
              type="text"
              placeholder="Workspace name…"
              value={newWsName}
              onChange={e => setNewWsName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') { setShowCreate(false); setNewWsName(''); } }}
              className="flex-1 border border-line-strong rounded-lg px-3 py-2 text-sm bg-surface text-fg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button onClick={submitCreate} className="px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700">Create</button>
            <button onClick={() => { setShowCreate(false); setNewWsName(''); }} className="px-3 py-2 text-fg-muted hover:text-fg text-sm">Cancel</button>
          </div>
        )}

        {/* Workspace grid */}
        {workspaces.length === 0 ? (
          <div className="text-center py-20 text-fg-subtle">
            <Grid3X3 size={48} className="mx-auto mb-4 opacity-30" />
            <p className="text-lg font-medium">No workspaces yet</p>
            <p className="text-sm mt-1">Create a workspace or try the demo to get started</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {workspaces.map(ws => (
              <div
                key={ws.id}
                className="bg-surface rounded-xl shadow-sm border border-line hover:shadow-md transition-shadow p-5 flex flex-col gap-3"
              >
                {renamingId === ws.id ? (
                  <div className="flex gap-2">
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={e => setRenameValue(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') submitRename(ws.id); if (e.key === 'Escape') setRenamingId(null); }}
                      className="flex-1 border border-line-strong rounded px-2 py-1 text-sm bg-surface text-fg focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button onClick={() => submitRename(ws.id)} className="text-accent text-sm font-medium">Save</button>
                    <button onClick={() => setRenamingId(null)} className="text-fg-subtle text-sm">✕</button>
                  </div>
                ) : (
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-base font-bold text-fg leading-snug">{ws.name}</h3>
                    <div className="flex gap-1 shrink-0">
                      <button
                        onClick={() => { setRenamingId(ws.id); setRenameValue(ws.name); }}
                        className="p-1 text-fg-subtle hover:text-accent transition-colors"
                        title="Rename workspace"
                      >
                        <Edit3 size={14} />
                      </button>
                      <button
                        onClick={() => setPendingDelete({ id: ws.id, name: ws.name })}
                        className="p-1 text-fg-subtle hover:text-red-500 transition-colors"
                        title="Delete workspace"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                )}
                <p className="text-sm text-fg-muted">{ws.boardCount} board{ws.boardCount !== 1 ? 's' : ''}</p>
                <button
                  onClick={() => onOpenWorkspace(ws.id, ws.name)}
                  className="mt-auto w-full py-2 bg-accent/10 hover:bg-accent/20 text-accent text-sm font-semibold rounded-lg transition-colors"
                >
                  Open →
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <DeleteConfirmationModal
        show={pendingDelete !== null}
        itemName={pendingDelete?.name ?? ''}
        onConfirm={() => {
          if (pendingDelete) onDeleteWorkspace(pendingDelete.id);
          setPendingDelete(null);
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
};

import React, { useState } from 'react';
import { DeleteConfirmationModal } from '../common/DeleteConfirmationModal';
import { BoardHeader } from './BoardHeader';
import { TeamMembersPanel } from './TeamMembersPanel';
import { EmptyBoardPitch } from './EmptyBoardPitch';
import { ViewSwitcher } from './views/ViewSwitcher';
import { KanbanView } from './kanban/KanbanView';
import { TimelineView } from './views/TimelineView';
import { MatrixView } from './views/MatrixView';
import { FeedView } from './views/FeedView';
import { FocusView } from './views/FocusView';
import { AIAssistantPanel } from './AIAssistantPanel';
import { NoteImportModal } from './notes/NoteImportModal';
import { getDemoNote } from '../../data/demoNotes';

export const KanbanBoard = ({ board, onUpdateBoard, onToggleCardCompletion, onReloadBoard, workspaceName }) => {
  const [boardView, setBoardView] = useState('kanban');
  const [editingProject, setEditingProject] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState({ show: false, id: null as any, name: '' });

  // A ready-made note, but ONLY for demo-workspace boards. getDemoNote requires both the demo
  // workspace name and a known board title, so a user's own workspace can never surface this.
  const sampleNote = getDemoNote(workspaceName ?? '', board.projectInfo?.title ?? '');

  // The board's columns as the import picker needs them: {id, title}, in board order.
  const importColumns = (board.columnOrder ?? []).map((id: string) => ({ id, title: board.columns[id]?.title ?? id }));

  const confirmDeleteMember = () => {
    const updatedCards = Object.fromEntries(
      Object.entries(board.cards).map(([id, c]: [string, any]) => [
        id, { ...c, assignees: c.assignees.filter((aId: any) => aId !== confirmDelete.id) },
      ])
    );
    onUpdateBoard({ ...board, cards: updatedCards, teamMembers: board.teamMembers.filter((m: any) => m.id !== confirmDelete.id) });
    setConfirmDelete({ show: false, id: null, name: '' });
  };

  return (
    <div className="min-h-screen bg-app text-fg pb-32">
      <div className="p-6">
        <div className="max-w-7xl mx-auto">
          <DeleteConfirmationModal
            show={confirmDelete.show}
            itemName={confirmDelete.name}
            onCancel={() => setConfirmDelete({ show: false, id: null, name: '' })}
            onConfirm={confirmDeleteMember}
          />

          <div className="mb-6 flex items-start justify-between gap-4">
            <div className="flex-1">
              <BoardHeader
                projectInfo={board.projectInfo}
                editing={editingProject}
                onStartEdit={() => setEditingProject(true)}
                onSave={(title, description) => {
                  onUpdateBoard({ ...board, projectInfo: { title: title.trim(), description: description.trim() } });
                  setEditingProject(false);
                }}
                onCancel={() => setEditingProject(false)}
              />
            </div>
            <button
              onClick={() => setShowImport(true)}
              className="shrink-0 rounded-md bg-accent px-3 py-2 text-sm font-semibold text-fg-inverted transition-colors hover:bg-accent-hover"
            >
              📥 Import notes
            </button>
          </div>

          {showImport && (
            <NoteImportModal
              boardId={board.id}
              boardVersion={board.version ?? null}
              members={board.teamMembers ?? []}
              columns={importColumns}
              onClose={() => setShowImport(false)}
              onBoardChanged={() => onReloadBoard?.()}
              sampleNote={sampleNote}
            />
          )}

          <div className="mb-6">
            <TeamMembersPanel
              members={board.teamMembers}
              onAddMember={member => onUpdateBoard({ ...board, teamMembers: [...board.teamMembers, member] })}
              onDeleteMember={id => {
                const m = board.teamMembers.find((m: any) => m.id === id);
                setConfirmDelete({ show: true, id, name: m?.name ?? '' });
              }}
            />
          </div>

          {/* Onboarding lives in the empty state: it appears exactly when someone has nothing to
              look at and is wondering what to do, and vanishes for good once a card exists. */}
          {Object.keys(board.cards ?? {}).length === 0 && (
            <EmptyBoardPitch onImport={() => setShowImport(true)} canImport={importColumns.length > 0} />
          )}

          <div className="mb-6">
            <ViewSwitcher currentView={boardView} onViewChange={setBoardView} />
          </div>

          {boardView === 'kanban'   && <KanbanView board={board} onUpdateBoard={onUpdateBoard} onToggleCardCompletion={onToggleCardCompletion} />}
          {boardView === 'timeline' && <TimelineView board={board} />}
          {boardView === 'matrix'   && <MatrixView board={board} />}
          {boardView === 'feed'     && <FeedView board={board} />}
          {boardView === 'focus'    && <FocusView board={board} onUpdateBoard={onUpdateBoard} />}
        </div>
      </div>

      <AIAssistantPanel
        board={board}
        onNavigate={view => {
          setBoardView(view);
          // The drawer sits at the bottom of a long page. Without this you switch to a shorter
          // view and keep the old scroll offset, landing below its content.
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }}
      />
    </div>
  );
};

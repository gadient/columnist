import React, { useState, useEffect, useRef } from 'react';
import { Plus, FolderPlus, Trash2 } from 'lucide-react';
import { DeleteConfirmationModal } from '../common/DeleteConfirmationModal';
import { BoardCreationWizard } from '../wizard/BoardCreationWizard';

// The board fields this grid actually reads — enough to type the map without pulling in the full
// Board model (without it, `Object.values(boards)` is `unknown` and every field access fails tsc).
interface BoardSummary {
  id: string;
  cards: Record<string, { completed?: boolean }>;
  projectInfo: { title: string; description?: string };
  updatedAt: string;
  teamMembers: { id: string; color: string; initials: string; name: string }[];
}

interface WorkspaceBoardsViewProps {
  workspaceName: string;
  boards: Record<string, BoardSummary>;
  onSelectBoard: (id: string) => void;
  onCreateBoard: (board: any) => void;
  onDeleteBoard: (id: string) => void;
}

export const WorkspaceBoardsView = ({ workspaceName, boards, onSelectBoard, onCreateBoard, onDeleteBoard }: WorkspaceBoardsViewProps) => {
  const [showCreationWizard, setShowCreationWizard] = useState(false);
  const [confirmDeleteBoard, setConfirmDeleteBoard] = useState<{ show: boolean; id: string | null; name: string }>({ show: false, id: null, name: '' });
  const wizardRef = useRef<HTMLDivElement>(null);
  const boardsList: BoardSummary[] = Object.values(boards);

  useEffect(() => {
    if (showCreationWizard && wizardRef.current) {
      wizardRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [showCreationWizard]);

  const formatDate = (dateString) => new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric'
  });

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-gray-900 dark:to-gray-950 p-6">
      <DeleteConfirmationModal
        show={confirmDeleteBoard.show}
        itemName={confirmDeleteBoard.name}
        onCancel={() => setConfirmDeleteBoard({ show: false, id: null, name: '' })}
        onConfirm={() => {
          if (confirmDeleteBoard.id) onDeleteBoard(confirmDeleteBoard.id);
          setConfirmDeleteBoard({ show: false, id: null, name: '' });
        }}
      />
      <div className="max-w-7xl mx-auto">
        <div className="mb-8 flex items-center justify-between flex-wrap gap-4">
          <h1 className="text-3xl font-bold text-fg">{workspaceName}</h1>
          <button
            onClick={() => setShowCreationWizard(v => !v)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Plus size={16} /> New Board
          </button>
        </div>

        <div ref={wizardRef}>
          {showCreationWizard && (
            <BoardCreationWizard
              onCreateBoard={(newBoard) => { onCreateBoard(newBoard); setShowCreationWizard(false); }}
              onCancel={() => setShowCreationWizard(false)}
            />
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {boardsList.map(board => {
            const cardCount = Object.keys(board.cards).length;
            const completedCards = Object.values(board.cards).filter(c => c.completed).length;
            return (
              <div
                key={board.id}
                className="bg-surface rounded-lg shadow-sm border border-line hover:shadow-md transition-shadow group cursor-pointer"
                onClick={() => onSelectBoard(board.id)}
              >
                <div className="p-6">
                  <div className="flex justify-between items-start mb-3">
                    <h3 className="text-lg font-semibold text-fg group-hover:text-accent transition-colors">
                      {board.projectInfo.title}
                    </h3>
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDeleteBoard({ show: true, id: board.id, name: board.projectInfo.title }); }}
                      className="text-fg-subtle hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100"
                      title="Delete board"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  <p className="text-fg-muted text-sm mb-4 line-clamp-2">{board.projectInfo.description}</p>
                  <div className="space-y-1 text-sm text-fg-muted mb-4">
                    <div className="flex justify-between"><span>Cards:</span><span className="font-medium">{cardCount}</span></div>
                    <div className="flex justify-between"><span>Done:</span><span className="font-medium text-green-600">{completedCards}</span></div>
                    <div className="flex justify-between"><span>Progress:</span><span className="font-medium">{cardCount > 0 ? Math.round((completedCards / cardCount) * 100) : 0}%</span></div>
                  </div>
                  <div className="pt-3 border-t border-line flex justify-between items-center text-xs text-fg-subtle">
                    <span>Updated {formatDate(board.updatedAt)}</span>
                    <div className="flex -space-x-1">
                      {board.teamMembers.slice(0, 3).map(m => (
                        <div key={m.id} className={`w-6 h-6 rounded-full ${m.color} flex items-center justify-center text-white text-xs font-medium border border-white`} title={m.name}>{m.initials}</div>
                      ))}
                      {board.teamMembers.length > 3 && <div className="w-6 h-6 rounded-full bg-gray-400 flex items-center justify-center text-white text-xs font-medium border border-white">+{board.teamMembers.length - 3}</div>}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          <div
            onClick={() => setShowCreationWizard(true)}
            className="bg-surface-sunken border-2 border-dashed border-line-strong rounded-lg hover:border-accent hover:bg-accent/5 transition-colors cursor-pointer flex flex-col items-center justify-center p-12 min-h-[200px]"
          >
            <FolderPlus size={40} className="text-fg-subtle mb-3" />
            <h3 className="text-base font-medium text-fg-muted">Create New Board</h3>
          </div>
        </div>
      </div>
    </div>
  );
};

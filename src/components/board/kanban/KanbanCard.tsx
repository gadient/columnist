import React from 'react';
import { Trash2, Clock, AlertOctagon, Link2 } from 'lucide-react';
import { PRIORITY_COLORS } from '../../../constants/colors';
import { formatDueDate, getDateColor } from '../../../utils/dates';

interface KanbanCardProps {
  card: any;
  members: any[];
  onEdit: () => void;
  onDelete: () => void;
  onToggleComplete: () => void;
  onDragStart: (e: React.DragEvent) => void;
}

const JIRA_COLORS: Record<string, string> = {
  ENG: 'bg-blue-700',
  PM: 'bg-purple-700',
  DES: 'bg-pink-700',
  LEGAL: 'bg-yellow-700',
  MKT: 'bg-green-700',
  SALES: 'bg-orange-700',
};

const jiraBadgeColor = (key: string) => {
  const prefix = key.split('-')[0];
  return JIRA_COLORS[prefix] ?? 'bg-indigo-700';
};

export const KanbanCard = ({ card, members, onEdit, onDelete, onToggleComplete, onDragStart }: KanbanCardProps) => {
  // Click-to-open: the whole card body opens the editor (the common kanban convention). A card is also
  // draggable, so we must not treat the tail of a drag as an "open" click — record the pointer-down
  // position and only open if the pointer barely moved. Interactive children (checkbox, delete)
  // stopPropagation so they never bubble up to this handler.
  const pointerDown = React.useRef<{ x: number; y: number } | null>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    pointerDown.current = { x: e.clientX, y: e.clientY };
  };

  const handleClick = (e: React.MouseEvent) => {
    const start = pointerDown.current;
    pointerDown.current = null;
    if (start && (Math.abs(e.clientX - start.x) > 5 || Math.abs(e.clientY - start.y) > 5)) {
      return; // moved too far — this was a drag, not a click
    }
    onEdit();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onEdit();
    }
  };

  return (
  <div
    draggable
    onDragStart={onDragStart}
    onMouseDown={handleMouseDown}
    onClick={handleClick}
    onKeyDown={handleKeyDown}
    role="button"
    tabIndex={0}
    aria-label={`Open card: ${card.title}`}
    className={`bg-surface-raised border-2 border-line-strong rounded-xl p-4 cursor-pointer hover:border-blue-500/60 hover:shadow-lg focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${card.completed ? 'opacity-50' : ''}`}
  >
    {/* Jira-style issue key + story points row */}
    {(card.jiraKey || card.storyPoints != null) && (
      <div className="flex items-center gap-2 mb-2">
        {card.jiraKey && (
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${jiraBadgeColor(card.jiraKey)} text-fg-inverted font-mono tracking-wide`}>
            {card.jiraKey}
          </span>
        )}
        {card.storyPoints != null && (
          <span className="text-[10px] font-bold w-5 h-5 rounded-full bg-yellow-500 text-gray-900 flex items-center justify-center ml-auto shrink-0" title="Story points">
            {card.storyPoints}
          </span>
        )}
      </div>
    )}

    {/* Title row */}
    <div className="flex items-start justify-between mb-2">
      <div className="flex items-start gap-2 flex-1 min-w-0">
        <input
          type="checkbox" checked={card.completed} onChange={onToggleComplete}
          onClick={e => e.stopPropagation()}
          className="mt-1 cursor-pointer accent-blue-500"
        />
        <h4 className={`font-medium text-sm text-fg leading-snug ${card.completed ? 'line-through text-fg-muted' : ''}`}>
          {card.title}
        </h4>
      </div>
      <div className="flex gap-1.5 shrink-0 ml-1">
        <button
          onClick={e => { e.stopPropagation(); onDelete(); }}
          aria-label="Delete card"
          className="text-fg-muted hover:text-red-600 dark:hover:text-red-400 transition-colors"
        ><Trash2 size={14} /></button>
      </div>
    </div>

    {card.description && (
      <p className="text-xs text-fg-subtle mb-3 leading-relaxed line-clamp-2">{card.description}</p>
    )}

    {/* Priority + due date */}
    <div className="flex flex-wrap gap-1.5 mb-2">
      <span className={`text-xs px-2 py-0.5 rounded-full border ${PRIORITY_COLORS[card.priority]}`}>
        {card.priority}
      </span>
      {card.dueDate && (
        <span className={`text-xs px-2 py-0.5 rounded-full flex items-center gap-1 ${getDateColor(card.dueDate)}`}>
          <Clock size={10} />
          {formatDueDate(card.dueDate)}
        </span>
      )}
    </div>

    {/* Labels */}
    {card.labels?.length > 0 && (
      <div className="flex flex-wrap gap-1 mb-2">
        {card.labels.slice(0, 4).map((l: string) => (
          <span key={l} className="text-[10px] px-1.5 py-0.5 bg-surface-raised text-fg-muted rounded-full">{l}</span>
        ))}
      </div>
    )}

    {/* Dependency indicators + assignees */}
    <div className="flex items-center justify-between mt-2">
      <div className="flex gap-2">
        {card.blockedBy?.length > 0 && (
          <span className="flex items-center gap-1 text-[10px] text-red-600 dark:text-red-400" title={`Blocked by ${card.blockedBy.length} card(s)`}>
            <AlertOctagon size={11} /> {card.blockedBy.length}
          </span>
        )}
        {card.dependsOn?.length > 0 && (
          <span className="flex items-center gap-1 text-[10px] text-blue-600 dark:text-blue-400" title={`Depends on ${card.dependsOn.length} card(s)`}>
            <Link2 size={11} /> {card.dependsOn.length}
          </span>
        )}
      </div>

      {card.assignees?.length > 0 && (
        <div className="flex -space-x-1.5">
          {card.assignees.map((aId: any) => {
            const m = members.find(m => m.id === aId);
            return m ? (
              <div
                key={m.id}
                className={`w-6 h-6 rounded-full ${m.color} flex items-center justify-center text-fg-inverted text-[10px] font-bold border-2 border-line`}
                title={m.name}
              >
                {m.initials}
              </div>
            ) : null;
          })}
        </div>
      )}
    </div>
  </div>
  );
};

import React, { useState } from 'react';
import { Clock, CheckCircle, SkipForward } from 'lucide-react';
import { daysUntilDue } from '../../../utils/dates';

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };

const sortForFocus = (cards: any[]) =>
  [...cards]
    .filter(c => !c.completed)
    .sort((a, b) => {
      // Overdue first
      const aOver = a.dueDate && (daysUntilDue(a.dueDate) ?? 0) < 0 ? -1 : 0;
      const bOver = b.dueDate && (daysUntilDue(b.dueDate) ?? 0) < 0 ? -1 : 0;
      if (aOver !== bOver) return aOver - bOver;
      // Then by priority
      const pd = PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority];
      if (pd !== 0) return pd;
      // Then by due date
      if (a.dueDate && b.dueDate) return a.dueDate.localeCompare(b.dueDate);
      return 0;
    });

const priorityGradient = (p) => {
  if (p === 'high') return 'from-red-600 to-purple-700';
  if (p === 'medium') return 'from-blue-600 to-purple-600';
  return 'from-teal-600 to-blue-600';
};

export const FocusView = ({ board, onUpdateBoard }) => {
  const allCards = Object.values(board.cards) as any[];
  const queue = sortForFocus(allCards);
  const [idx, setIdx] = useState(0);
  const members = board.teamMembers;

  const current = queue[idx] ?? null;
  const upNext = queue.slice(idx + 1, idx + 5);

  const markComplete = () => {
    if (!current) return;
    const updatedCards = {
      ...board.cards,
      [current.id]: { ...current, completed: true }
    };
    onUpdateBoard({ ...board, cards: updatedCards });
    // Stay at same index (next card slides in)
  };

  if (!current) {
    return (
      <div className="text-center py-24">
        <div className="text-6xl mb-4">🎉</div>
        <h2 className="text-2xl font-bold text-fg mb-2">All tasks complete!</h2>
        <p className="text-fg-subtle">Great work — take a well-deserved break.</p>
      </div>
    );
  }

  const cardMembers = members.filter(m => current.assignees.includes(m.id));
  const daysUntil = current.dueDate ? daysUntilDue(current.dueDate) : null;

  const dueLine = daysUntil === null ? null
    : daysUntil < 0 ? `Overdue by ${Math.abs(daysUntil)} day${Math.abs(daysUntil) !== 1 ? 's' : ''}`
    : daysUntil === 0 ? 'Due today'
    : daysUntil === 1 ? 'Due tomorrow'
    : `Due in ${daysUntil} days`;

  return (
    <div className="flex gap-6">
      {/* Spotlight */}
      <div className={`flex-1 bg-gradient-to-br ${priorityGradient(current.priority)} rounded-2xl p-10 relative overflow-hidden`}>
        <div className="absolute -top-16 -right-16 w-64 h-64 bg-white/10 rounded-full" />
        <div className="relative z-10">
          <p className="text-fg-inverted/70 text-xs uppercase tracking-widest font-semibold mb-4">Now focusing on</p>
          <h2 className="text-3xl font-bold text-fg-inverted mb-3 leading-tight">{current.title}</h2>
          {current.description && (
            <p className="text-fg-inverted/80 text-base leading-relaxed mb-6">{current.description}</p>
          )}

          <div className="flex flex-wrap gap-4 mb-8 text-fg-inverted/80 text-sm">
            {dueLine && (
              <span className="flex items-center gap-1.5">
                <Clock size={14} />
                {dueLine}
              </span>
            )}
            <span className="capitalize font-semibold">{current.priority} priority</span>
            {cardMembers.length > 0 && (
              <div className="flex -space-x-2">
                {cardMembers.map(m => (
                  <div
                    key={m.id}
                    className={`w-7 h-7 rounded-full ${m.color} flex items-center justify-center text-fg-inverted text-xs font-bold border-2 border-white/30`}
                    title={m.name}
                  >
                    {m.initials}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex gap-3">
            <button
              onClick={markComplete}
              className="flex items-center gap-2 bg-surface text-fg px-6 py-3 rounded-xl font-semibold hover:bg-surface-raised transition-all shadow-lg"
            >
              <CheckCircle size={18} />
              Mark Complete
            </button>
            <button
              onClick={() => setIdx(i => Math.min(i + 1, queue.length - 1))}
              disabled={idx >= queue.length - 1}
              className="flex items-center gap-2 bg-white/20 text-fg-inverted px-6 py-3 rounded-xl font-semibold hover:bg-white/30 transition-all disabled:opacity-40"
            >
              <SkipForward size={18} />
              Skip
            </button>
          </div>
        </div>
      </div>

      {/* Up Next */}
      <div className="w-72 shrink-0">
        <h3 className="text-xs uppercase text-fg-muted font-semibold tracking-widest mb-3">Up Next</h3>
        <div className="space-y-2">
          {upNext.map((card, i) => {
            const days = card.dueDate ? daysUntilDue(card.dueDate) : null;
            return (
              <div
                key={card.id}
                onClick={() => setIdx(idx + i + 1)}
                className="bg-surface p-4 rounded-xl border border-line hover:border-blue-500/60 transition-all cursor-pointer"
              >
                <p className="font-semibold text-fg text-sm leading-snug mb-1.5 line-clamp-2">{card.title}</p>
                <div className="flex justify-between items-center text-xs text-fg-muted">
                  <span className="capitalize">{card.priority}</span>
                  {days !== null && (
                    <span className={days < 0 ? 'text-red-600 dark:text-red-400' : ''}>
                      {days < 0 ? `Overdue` : days === 0 ? 'Today' : `${days}d`}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
          {upNext.length === 0 && (
            <p className="text-fg-muted text-sm text-center py-8">No more tasks</p>
          )}
        </div>
      </div>
    </div>
  );
};

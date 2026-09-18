import React, { useState } from 'react';
import { Clock, CheckCircle } from 'lucide-react';
import { parseLocalDate, daysUntilDue } from '../../../utils/dates';

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };

const priorityBadge = (p) => {
  if (p === 'high') return 'bg-red-500/20 text-red-700 dark:text-red-400 border border-red-500/30';
  if (p === 'medium') return 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-400 border border-yellow-500/30';
  return 'bg-green-500/20 text-green-700 dark:text-green-400 border border-green-500/30';
};

const FILTERS = [
  { id: 'all',    label: 'All' },
  { id: 'urgent', label: 'Urgent' },
  { id: 'week',   label: 'This Week' },
  { id: 'done',   label: 'Completed' },
];

export const FeedView = ({ board }) => {
  const [filter, setFilter] = useState('all');
  const cards = Object.values(board.cards) as any[];
  const members = board.teamMembers;

  const filtered = cards
    .filter(c => {
      if (filter === 'all') return !c.completed;
      if (filter === 'done') return c.completed;
      if (filter === 'urgent') return !c.completed && c.priority === 'high';
      if (filter === 'week') {
        if (c.completed || !c.dueDate) return false;
        const d = daysUntilDue(c.dueDate);
        return d !== null && d <= 7;   // due within a week (incl. overdue)
      }
      return true;
    })
    .sort((a, b) => {
      if (filter === 'done') return 0;
      const pd = PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority];
      if (pd !== 0) return pd;
      if (a.dueDate && b.dueDate) return a.dueDate.localeCompare(b.dueDate);
      return 0;
    });

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex gap-2 mb-6 p-3 bg-surface rounded-xl overflow-x-auto">
        {FILTERS.map(f => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all whitespace-nowrap ${
              filter === f.id
                ? 'bg-gradient-to-r from-blue-500 to-purple-500 text-fg-inverted shadow'
                : 'text-fg-subtle hover:text-fg hover:bg-surface-raised'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {filtered.map(card => {
          const cardMembers = members.filter(m => card.assignees.includes(m.id));
          const daysUntil = card.dueDate ? daysUntilDue(card.dueDate) : null;
          const overdue = daysUntil !== null && daysUntil < 0 && !card.completed;

          return (
            <div
              key={card.id}
              className={`bg-surface rounded-xl p-5 border transition-all hover:border-blue-500/50 ${
                overdue ? 'border-red-500/40' : 'border-line'
              } ${card.completed ? 'opacity-50' : ''}`}
            >
              <div className="flex justify-between items-start gap-3 mb-2">
                <div className="flex items-start gap-2 flex-1 min-w-0">
                  {card.completed && <CheckCircle size={16} className="text-green-500 shrink-0 mt-0.5" />}
                  <h3 className={`font-semibold text-fg leading-snug ${card.completed ? 'line-through text-fg-subtle' : ''}`}>
                    {card.title}
                  </h3>
                </div>
                <span className={`text-xs px-2.5 py-1 rounded-full font-semibold shrink-0 ${priorityBadge(card.priority)}`}>
                  {card.priority.toUpperCase()}
                </span>
              </div>

              {card.description && (
                <p className="text-fg-subtle text-sm mb-3 leading-relaxed line-clamp-2">{card.description}</p>
              )}

              <div className="flex justify-between items-center">
                <div className="flex items-center gap-3 text-sm">
                  {card.dueDate && (
                    <span className={`flex items-center gap-1 ${overdue ? 'text-red-600 dark:text-red-400' : 'text-fg-muted'}`}>
                      <Clock size={12} />
                      {overdue
                        ? `Overdue · ${parseLocalDate(card.dueDate)?.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
                        : parseLocalDate(card.dueDate)?.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
                    </span>
                  )}
                </div>
                <div className="flex -space-x-2">
                  {cardMembers.map(m => (
                    <div
                      key={m.id}
                      className={`w-7 h-7 rounded-full ${m.color} flex items-center justify-center text-fg-inverted text-xs font-semibold border-2 border-line`}
                      title={m.name}
                    >
                      {m.initials}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          );
        })}

        {filtered.length === 0 && (
          <div className="text-center py-20 text-fg-muted">
            <CheckCircle size={48} className="mx-auto mb-4 opacity-30" />
            <p className="text-lg font-medium">Nothing here</p>
          </div>
        )}
      </div>
    </div>
  );
};

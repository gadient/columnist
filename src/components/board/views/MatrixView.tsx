import React from 'react';
import { parseLocalDate, daysUntilDue } from '../../../utils/dates';

const QUADRANTS = {
  q1: { title: 'Do First',  subtitle: 'Urgent · Important',       icon: '🔥', gradient: 'from-red-700 to-red-800',    border: 'border-red-500/40' },
  q2: { title: 'Schedule',  subtitle: 'Important · Not Urgent',   icon: '📅', gradient: 'from-yellow-700 to-yellow-800', border: 'border-yellow-500/40' },
  q3: { title: 'Delegate',  subtitle: 'Urgent · Not Important',   icon: '👥', gradient: 'from-blue-700 to-blue-800',  border: 'border-blue-500/40' },
  q4: { title: 'Eliminate', subtitle: 'Not Urgent · Not Important', icon: '🗑️', gradient: 'from-gray-700 to-gray-800', border: 'border-line-strong/40' },
};

const classify = (card) => {
  const days = card.dueDate ? (daysUntilDue(card.dueDate) ?? 999) : 999;
  const urgent = days <= 3;
  const important = card.priority === 'high';
  if (urgent && important) return 'q1';
  if (!urgent && important) return 'q2';
  if (urgent && !important) return 'q3';
  return 'q4';
};

export const MatrixView = ({ board }) => {
  const incompleteCards = (Object.values(board.cards) as any[]).filter(c => !c.completed);
  const members = board.teamMembers;

  const buckets: Record<string, any[]> = { q1: [], q2: [], q3: [], q4: [] };
  incompleteCards.forEach(c => buckets[classify(c)].push(c));

  return (
    <div className="grid grid-cols-2 gap-4">
      {(Object.entries(QUADRANTS) as [string, typeof QUADRANTS.q1][]).map(([key, q]) => (
        <div key={key} className={`bg-surface rounded-xl p-5 border ${q.border}`}>
          <div className="flex items-center gap-3 mb-4 pb-3 border-b border-line">
            <div className={`w-11 h-11 rounded-xl bg-gradient-to-br ${q.gradient} flex items-center justify-center text-xl shadow-inner`}>
              {q.icon}
            </div>
            <div>
              <h3 className="font-bold text-fg">{q.title}</h3>
              <p className="text-xs text-fg-subtle">{q.subtitle} · {buckets[key].length} tasks</p>
            </div>
          </div>

          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {buckets[key].map(card => {
              const cardMembers = members.filter(m => card.assignees.includes(m.id));
              return (
                <div key={card.id} className="bg-surface-raised hover:bg-surface-raised p-3 rounded-lg transition-colors">
                  <p className="font-medium text-fg text-sm leading-snug line-clamp-2 mb-1.5">{card.title}</p>
                  <div className="flex justify-between items-center">
                    <span className="text-xs text-fg-subtle">
                      {card.dueDate
                        ? parseLocalDate(card.dueDate)?.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                        : 'No date'}
                    </span>
                    <div className="flex -space-x-1">
                      {cardMembers.slice(0, 3).map(m => (
                        <div
                          key={m.id}
                          className={`w-6 h-6 rounded-full ${m.color} flex items-center justify-center text-fg-inverted text-[10px] font-bold border border-line`}
                          title={m.name}
                        >
                          {m.initials[0]}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              );
            })}
            {buckets[key].length === 0 && (
              <p className="text-fg-muted text-sm text-center py-6">All clear</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

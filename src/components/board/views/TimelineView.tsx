import React, { useState } from 'react';
import { ChevronLeft, ChevronRight, Clock } from 'lucide-react';
import { toLocalYMD } from '../../../utils/dates';

const priorityBorder = (p) =>
  p === 'high' ? 'border-l-red-500' : p === 'medium' ? 'border-l-yellow-500' : 'border-l-green-500';

const getMondayOf = (d: Date) => {
  const m = new Date(d);
  const day = m.getDay();
  m.setDate(m.getDate() - (day === 0 ? 6 : day - 1));
  m.setHours(0, 0, 0, 0);
  return m;
};

export const TimelineView = ({ board }) => {
  const today = new Date();
  const [weekStart, setWeekStart] = useState(() => getMondayOf(today));
  const cards = Object.values(board.cards) as any[];
  const members = board.teamMembers;

  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  const cardsForDay = (day: Date) => {
    const s = toLocalYMD(day);
    return cards.filter(c => c.dueDate === s);
  };

  const shift = (n: number) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + n);
    setWeekStart(d);
  };

  const endOfWeek = days[6];
  const rangeLabel = `${weekStart.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – ${endOfWeek.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}`;

  return (
    <div>
      <div className="flex justify-between items-center mb-5 bg-surface px-5 py-3 rounded-xl">
        <button onClick={() => shift(-7)} className="p-2 hover:bg-surface-raised rounded-lg transition-colors">
          <ChevronLeft size={18} className="text-fg-subtle" />
        </button>
        <span className="text-sm font-semibold text-fg">{rangeLabel}</span>
        <button onClick={() => shift(7)} className="p-2 hover:bg-surface-raised rounded-lg transition-colors">
          <ChevronRight size={18} className="text-fg-subtle" />
        </button>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-2">
        {days.map((day, i) => {
          const isToday = day.toDateString() === today.toDateString();
          const dayCards = cardsForDay(day);
          return (
            <div key={i} className="flex-1 min-w-[130px]">
              <div className={`text-center py-3 rounded-t-xl ${isToday ? 'bg-blue-600' : 'bg-surface'}`}>
                <div className="text-xs text-fg-subtle uppercase font-medium tracking-wide">
                  {day.toLocaleDateString('en-US', { weekday: 'short' })}
                </div>
                <div className={`text-2xl font-bold mt-0.5 ${isToday ? 'text-fg-inverted' : 'text-fg'}`}>
                  {day.getDate()}
                </div>
              </div>
              <div className="bg-surface rounded-b-xl p-2 min-h-[360px] space-y-2">
                {dayCards.map(card => (
                  <div
                    key={card.id}
                    className={`bg-surface-raised p-3 rounded-lg border-l-4 ${priorityBorder(card.priority)} ${card.completed ? 'opacity-40' : ''}`}
                  >
                    <p className="text-xs font-semibold text-fg leading-snug line-clamp-3">{card.title}</p>
                    <div className="flex items-center gap-1 mt-1.5 text-fg-muted">
                      <Clock size={10} />
                      <span className="text-[10px] capitalize">{card.priority}</span>
                    </div>
                    {card.assignees.length > 0 && (
                      <div className="flex -space-x-1 mt-1.5">
                        {card.assignees.slice(0, 3).map(aId => {
                          const m = members.find(m => m.id === aId);
                          return m ? (
                            <div
                              key={m.id}
                              className={`w-5 h-5 rounded-full ${m.color} flex items-center justify-center text-fg-inverted text-[9px] font-bold border border-line`}
                              title={m.name}
                            >
                              {m.initials[0]}
                            </div>
                          ) : null;
                        })}
                      </div>
                    )}
                  </div>
                ))}
                {dayCards.length === 0 && (
                  <div className="flex items-center justify-center h-20">
                    <span className="text-fg-subtle text-sm">—</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

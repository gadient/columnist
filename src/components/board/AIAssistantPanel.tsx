import React, { useState, useEffect } from 'react';
import { Sparkles, AlertTriangle, Bell, Lightbulb, TrendingUp, X, ChevronDown, ChevronUp, CheckCircle, ClipboardList, Loader2 } from 'lucide-react';
import { apiGetIntelligence } from '../../api/analytics';
import { daysUntilDue } from '../../utils/dates';

const FILTER_TABS = [
  { id: 'all',       label: 'All' },
  { id: 'insight',   label: 'Progress' },
  { id: 'warning',   label: 'Warnings' },
  { id: 'reminder',  label: 'Reminders' },
  { id: 'suggestion',label: 'Tips' },
];

const STYLE = {
  insight:    { border: 'border-blue-500',   bg: 'bg-blue-500/10',   icon: 'text-blue-600 dark:text-blue-400',     btn: 'bg-blue-500 hover:bg-blue-600' },
  warning:    { border: 'border-orange-500', bg: 'bg-orange-500/10', icon: 'text-orange-600 dark:text-orange-400', btn: 'bg-orange-500 hover:bg-orange-600' },
  reminder:   { border: 'border-purple-500', bg: 'bg-purple-500/10', icon: 'text-purple-600 dark:text-purple-400', btn: 'bg-purple-500 hover:bg-purple-600' },
  suggestion: { border: 'border-green-500',  bg: 'bg-green-500/10',  icon: 'text-green-600 dark:text-green-400',   btn: 'bg-green-500 hover:bg-green-600' },
};

const buildLocalInsights = (board) => {
  const cards = Object.values(board.cards) as any[];
  const insights: any[] = [];
  let id = 0;

  const incomplete = cards.filter(c => !c.completed);
  const completed  = cards.filter(c => c.completed);
  const overdue    = incomplete.filter(c => c.dueDate && (daysUntilDue(c.dueDate) ?? 0) < 0);
  const highPri    = incomplete.filter(c => c.priority === 'high');
  const dueSoon    = incomplete.filter(c => {
    if (!c.dueDate) return false;
    const d = daysUntilDue(c.dueDate);
    return d !== null && d >= 0 && d <= 2;
  });
  const blocked    = incomplete.filter(c => c.blockedBy?.length > 0);
  const rate = cards.length ? Math.round((completed.length / cards.length) * 100) : 0;

  insights.push({
    id: `i${id++}`, type: 'insight', icon: TrendingUp,
    title: 'Board Progress',
    message: `${rate}% complete (${completed.length} of ${cards.length} tasks). ${
      rate >= 70 ? 'Great momentum!' : rate >= 40 ? 'Steady progress — keep pushing.' : 'Lots still to do. Focus on high-priority items first.'
    }`,
    action: 'View Feed', target: 'feed',
  });

  if (blocked.length > 0) {
    const heldSP = blocked.reduce((s, c) => s + (c.storyPoints || 0), 0);
    insights.push({
      id: `i${id++}`, type: 'warning', icon: AlertTriangle,
      title: `${blocked.length} Card${blocked.length > 1 ? 's' : ''} Blocked`,
      message: `${blocked.length} task${blocked.length > 1 ? 's are' : ' is'} blocked${heldSP ? ` (${heldSP} story points held up)` : ''}. Resolve blockers before starting new work.`,
      action: 'Open Matrix', target: 'matrix',
    });
  }

  if (highPri.length > 0 || overdue.length > 0) {
    insights.push({
      id: `i${id++}`, type: 'warning', icon: AlertTriangle,
      title: overdue.length > 0 ? `${overdue.length} Overdue Task${overdue.length > 1 ? 's' : ''}` : 'High Priority Tasks',
      message: overdue.length > 0
        ? `"${overdue[0].title}"${overdue.length > 1 ? ` and ${overdue.length - 1} more are` : ' is'} past due. Address these before picking up new work.`
        : `${highPri.length} high-priority task${highPri.length > 1 ? 's are' : ' is'} open. Consider using Focus mode to work through them.`,
      // Branches with the copy above: the overdue wording is a triage case (Matrix), the
      // high-priority wording explicitly recommends Focus, so send it there.
      action: overdue.length > 0 ? 'Open Matrix' : 'Try Focus',
      target: overdue.length > 0 ? 'matrix' : 'focus',
    });
  }

  if (dueSoon.length > 0) {
    insights.push({
      id: `i${id++}`, type: 'reminder', icon: Bell,
      title: 'Due Very Soon',
      message: `${dueSoon.slice(0, 2).map(c => `"${c.title}"`).join(' and ')}${dueSoon.length > 2 ? ` (+${dueSoon.length - 2} more)` : ''} ${dueSoon.length === 1 ? 'is' : 'are'} due within 48 hours.`,
      action: 'Open Timeline', target: 'timeline',
    });
  }

  const workload: Record<string, number> = {};
  board.teamMembers.forEach(m => { workload[m.id] = 0; });
  incomplete.forEach(c => (c.assignees ?? []).forEach(aId => { workload[aId] = (workload[aId] || 0) + 1; }));
  const maxLoad = Math.max(...Object.values(workload), 0);
  const busiest = board.teamMembers.find(m => workload[m.id] === maxLoad);
  const avgLoad = board.teamMembers.length
    ? Object.values(workload).reduce((s, v) => s + v, 0) / board.teamMembers.length
    : 0;

  if (busiest && maxLoad > 0 && maxLoad > avgLoad * 1.6) {
    insights.push({
      id: `i${id++}`, type: 'suggestion', icon: Lightbulb,
      title: 'Workload Imbalance',
      message: `${busiest.name} has ${maxLoad} open tasks vs a team average of ${Math.round(avgLoad)}. Consider redistributing to balance capacity.`,
      action: 'View Feed', target: 'feed',
    });
  }

  if (rate < 30 && incomplete.length > 5) {
    insights.push({
      id: `i${id++}`, type: 'suggestion', icon: Lightbulb,
      title: 'Productivity Tip',
      message: 'Try Focus Mode to work through high-priority tasks one at a time — it reduces context-switching and builds momentum.',
      action: 'Try Focus', target: 'focus',
    });
  }

  return insights;
};

export const AIAssistantPanel = ({ board, onNavigate }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [filter, setFilter] = useState('all');
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [intelligence, setIntelligence] = useState<{ narrative: string } | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);
  // Distinguishes "not fetched yet" from "fetched and failed", so the panel never claims a failure
  // that has not happened.
  const [intelligenceTried, setIntelligenceTried] = useState(false);

  useEffect(() => {
    if (isOpen && !intelligence && !intelligenceLoading) {
      setIntelligenceLoading(true);
      apiGetIntelligence(board.id)
        .then(data => setIntelligence(data))
        .catch(() => {})
        .finally(() => { setIntelligenceLoading(false); setIntelligenceTried(true); });
    }
  }, [isOpen]);

  const all = buildLocalInsights(board);
  const visible = all
    .filter(n => !dismissed.has(n.id))
    .filter(n => filter === 'all' || n.type === filter);
  const unread = all.filter(n => !dismissed.has(n.id)).length;

  return (
    <div
      className={`fixed bottom-0 left-0 z-40 transition-all duration-300 ${isOpen ? 'translate-y-0' : 'translate-y-full'}`}
      style={{ right: 'var(--chat-panel-width, 0px)' }}
    >
      {/* Pull tab */}
      <button
        onClick={() => setIsOpen(v => !v)}
        className={`absolute -top-11 right-6 bg-gradient-to-r from-blue-500 to-purple-500 text-fg-inverted px-5 py-2.5 rounded-t-xl shadow-lg hover:shadow-xl transition-all flex items-center gap-2 ${
          !isOpen && unread > 0 ? 'animate-pulse' : ''
        }`}
      >
        <Sparkles size={16} />
        <span className="font-semibold text-sm">Board Insights</span>
        {unread > 0 && (
          <span className="bg-red-500 text-fg-inverted text-xs px-1.5 py-0.5 rounded-full font-bold">{unread}</span>
        )}
        {isOpen ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
      </button>

      {/* Panel body */}
      <div className="bg-surface border-t border-line shadow-2xl max-h-[420px] overflow-y-auto">
        <div className="max-w-7xl mx-auto px-6 py-5">
          {/* Header + filters */}
          <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-purple-500 rounded-lg flex items-center justify-center">
                <Sparkles size={18} className="text-fg-inverted" />
              </div>
              <div>
                <h3 className="font-bold text-fg text-sm">Board Insights</h3>
                <p className="text-xs text-fg-muted">Insights from your board data</p>
              </div>
            </div>
            <div className="flex gap-1.5 overflow-x-auto">
              {FILTER_TABS.map(t => (
                <button
                  key={t.id}
                  onClick={() => setFilter(t.id)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap ${
                    filter === t.id
                      ? 'bg-gradient-to-r from-blue-500 to-purple-500 text-fg-inverted'
                      : 'bg-surface text-fg-subtle hover:text-fg'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Board summary banner — written from board data by a fixed template, not a model. */}
          <div className="mb-4 border border-purple-500/40 bg-purple-500/10 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <ClipboardList size={16} className="text-purple-600 dark:text-purple-400" />
              <span className="text-xs font-semibold text-purple-700 dark:text-purple-300 uppercase tracking-wide">Board Summary</span>
            </div>
            {intelligenceLoading ? (
              <div className="flex items-center gap-2 text-fg-subtle text-xs">
                <Loader2 size={13} className="animate-spin" />
                Analyzing board data…
              </div>
            ) : intelligence ? (
              <p className="text-fg-muted text-xs leading-relaxed">{intelligence.narrative}</p>
            ) : intelligenceTried ? (
              <p className="text-fg-muted text-xs">Couldn't build the summary right now.</p>
            ) : null}
          </div>

          {/* Local insight cards */}
          <div className="flex gap-4 overflow-x-auto pb-2">
            {visible.map(n => {
              const Icon = n.icon;
              const s = STYLE[n.type] || STYLE.insight;
              return (
                <div
                  key={n.id}
                  className={`flex-shrink-0 w-72 border-l-4 ${s.border} ${s.bg} rounded-xl p-4 backdrop-blur-sm`}
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Icon size={16} className={s.icon} />
                      <span className="font-semibold text-fg text-sm">{n.title}</span>
                    </div>
                    <button
                      onClick={() => setDismissed(prev => new Set([...prev, n.id]))}
                      className="text-fg-muted hover:text-fg transition-colors ml-2 shrink-0"
                    >
                      <X size={14} />
                    </button>
                  </div>
                  <p className="text-fg-muted text-xs leading-relaxed mb-3">{n.message}</p>
                  {/* The action takes you to the view that shows the thing the insight is about,
                      then gets the drawer out of the way so you can actually see it. */}
                  <button
                    onClick={() => {
                      onNavigate?.(n.target);
                      setIsOpen(false);
                    }}
                    className={`w-full py-1.5 rounded-lg text-xs font-semibold text-fg-inverted transition-all ${s.btn}`}
                  >
                    {n.action}
                  </button>
                </div>
              );
            })}

            {visible.length === 0 && (
              <div className="flex items-center gap-3 py-4 text-fg-muted">
                <CheckCircle size={24} className="opacity-40" />
                <span className="text-sm">No insights in this category</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

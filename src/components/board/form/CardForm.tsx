import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Tag, Link2, AlertOctagon } from 'lucide-react';
import { PriorityPicker } from './PriorityPicker';
import { AssigneePicker } from './AssigneePicker';
import { DateField } from './DateField';

const SP_OPTIONS = [1, 2, 3, 5, 8, 13, 21];

interface CardFormProps {
  title: string;
  description: string;
  priority: string;
  dueDate: string;
  assignees: any[];
  members: any[];
  storyPoints: number | null;
  jiraKey: string;
  labels: string[];
  blockedBy: string[];
  dependsOn: string[];
  allCards: { id: string; title: string }[];
  onChangeTitle: (v: string) => void;
  onChangeDescription: (v: string) => void;
  onChangePriority: (v: any) => void;
  onChangeDueDate: (v: string) => void;
  onToggleAssignee: (id: any) => void;
  onChangeStoryPoints: (v: number | null) => void;
  onChangeJiraKey: (v: string) => void;
  onChangeLabels: (v: string[]) => void;
  onToggleBlockedBy: (id: string) => void;
  onToggleDependsOn: (id: string) => void;
  onSave: () => void;
  onCancel: () => void;
  saveLabel?: string;
  isNew?: boolean;
}

export const CardForm = ({
  title, description, priority, dueDate, assignees, members,
  storyPoints, jiraKey, labels, blockedBy, dependsOn, allCards,
  onChangeTitle, onChangeDescription, onChangePriority, onChangeDueDate, onToggleAssignee,
  onChangeStoryPoints, onChangeJiraKey, onChangeLabels, onToggleBlockedBy, onToggleDependsOn,
  onSave, onCancel, saveLabel = '+ Add Card', isNew = false,
}: CardFormProps) => {
  const [showAdvanced, setShowAdvanced] = useState(!isNew && (!!jiraKey || !!storyPoints || labels.length > 0 || blockedBy.length > 0 || dependsOn.length > 0));
  const [labelInput, setLabelInput] = useState('');

  const addLabel = () => {
    const trimmed = labelInput.trim().toLowerCase();
    if (trimmed && !labels.includes(trimmed)) {
      onChangeLabels([...labels, trimmed]);
    }
    setLabelInput('');
  };

  const removeLabel = (l: string) => onChangeLabels(labels.filter(x => x !== l));

  const otherCards = allCards.filter(c => c.title !== title);

  return (
    <div className={`bg-surface-raised border-2 ${isNew ? 'border-blue-500/60' : 'border-blue-500'} rounded-xl p-4 space-y-4`}>
      {/* Core fields */}
      <div>
        <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide">Task Title</label>
        <input
          autoFocus type="text" placeholder="What needs to be done?" value={title}
          onChange={e => onChangeTitle(e.target.value)}
          className="w-full px-3 py-2.5 bg-surface-raised border-2 border-line-strong rounded-lg text-sm text-fg placeholder:text-fg-subtle focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
        />
      </div>
      <div>
        <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide">Description</label>
        <textarea
          placeholder="Add more details..." value={description} rows={isNew ? 2 : 3}
          onChange={e => onChangeDescription(e.target.value)}
          className="w-full px-3 py-2.5 bg-surface-raised border-2 border-line-strong rounded-lg text-sm text-fg placeholder:text-fg-subtle resize-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
        />
      </div>
      <div>
        <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide">Priority</label>
        <PriorityPicker value={priority as any} onChange={onChangePriority} />
      </div>
      <div>
        <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide">Due Date</label>
        <DateField value={dueDate} onChange={onChangeDueDate} />
      </div>
      {members.length > 0 && (
        <div>
          <label className="block text-xs font-semibold text-fg-subtle mb-2 uppercase tracking-wide">Assigned To</label>
          <AssigneePicker members={members} selected={assignees} onChange={onToggleAssignee} />
        </div>
      )}

      {/* Advanced / issue-key section */}
      <div className="border-t border-line-strong pt-3">
        <button
          type="button"
          onClick={() => setShowAdvanced(v => !v)}
          className="flex items-center gap-2 text-xs text-fg-subtle hover:text-fg-muted transition-colors font-semibold uppercase tracking-wide w-full text-left"
        >
          {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          Issue key, estimate & dependencies {(jiraKey || storyPoints || labels.length > 0 || blockedBy.length > 0 || dependsOn.length > 0) && (
            <span className="ml-auto text-purple-600 dark:text-purple-400 normal-case font-normal tracking-normal">configured</span>
          )}
        </button>

        {showAdvanced && (
          <div className="mt-4 space-y-4">
            {/* Jira-style issue key + Story points */}
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide">Issue key (Jira-style)</label>
                <input
                  type="text" placeholder="ENG-1234" value={jiraKey}
                  onChange={e => onChangeJiraKey(e.target.value.toUpperCase())}
                  className="w-full px-3 py-2 bg-surface-raised border border-line-strong rounded-lg text-sm text-purple-700 dark:text-purple-300 placeholder:text-fg-subtle focus:ring-2 focus:ring-purple-500 outline-none font-mono"
                />
              </div>
              <div className="w-36">
                <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide">Story Points</label>
                <div className="flex gap-1 flex-wrap">
                  {SP_OPTIONS.map(sp => (
                    <button
                      key={sp}
                      type="button"
                      onClick={() => onChangeStoryPoints(storyPoints === sp ? null : sp)}
                      className={`w-8 h-8 rounded-lg text-xs font-bold transition-all ${
                        storyPoints === sp
                          ? 'bg-yellow-500 text-gray-900'
                          : 'bg-surface-raised text-fg-subtle hover:bg-surface-raised'
                      }`}
                    >
                      {sp}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Labels */}
            <div>
              <label className="block text-xs font-semibold text-fg-subtle mb-1.5 uppercase tracking-wide flex items-center gap-1">
                <Tag size={11} /> Labels
              </label>
              <div className="flex gap-2 flex-wrap mb-2">
                {labels.map(l => (
                  <span key={l} className="flex items-center gap-1 bg-surface-raised text-fg-muted text-xs px-2 py-1 rounded-full">
                    {l}
                    <button type="button" onClick={() => removeLabel(l)} className="text-fg-subtle hover:text-fg ml-0.5">×</button>
                  </span>
                ))}
              </div>
              <div className="flex gap-2">
                <input
                  type="text" placeholder="Add label…" value={labelInput}
                  onChange={e => setLabelInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addLabel(); } }}
                  className="flex-1 px-3 py-1.5 bg-surface-raised border border-line-strong rounded-lg text-xs text-fg placeholder:text-fg-subtle focus:ring-1 focus:ring-blue-500 outline-none"
                />
                <button type="button" onClick={addLabel} className="px-3 py-1.5 bg-surface-raised text-fg-muted rounded-lg text-xs hover:bg-surface-raised transition-colors">
                  Add
                </button>
              </div>
            </div>

            {/* Blocked by */}
            {otherCards.length > 0 && (
              <div>
                <label className="block text-xs font-semibold text-red-600 dark:text-red-400 mb-1.5 uppercase tracking-wide flex items-center gap-1">
                  <AlertOctagon size={11} /> Blocked By
                </label>
                <div className="max-h-28 overflow-y-auto space-y-1 bg-surface rounded-lg p-2">
                  {otherCards.map(c => (
                    <label key={c.id} className="flex items-center gap-2 cursor-pointer hover:bg-surface-raised rounded px-1 py-0.5">
                      <input
                        type="checkbox"
                        checked={blockedBy.includes(c.id)}
                        onChange={() => onToggleBlockedBy(c.id)}
                        className="accent-red-500"
                      />
                      <span className="text-xs text-fg-muted truncate">{c.title}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Depends on */}
            {otherCards.length > 0 && (
              <div>
                <label className="block text-xs font-semibold text-blue-600 dark:text-blue-400 mb-1.5 uppercase tracking-wide flex items-center gap-1">
                  <Link2 size={11} /> Depends On
                </label>
                <div className="max-h-28 overflow-y-auto space-y-1 bg-surface rounded-lg p-2">
                  {otherCards.map(c => (
                    <label key={c.id} className="flex items-center gap-2 cursor-pointer hover:bg-surface-raised rounded px-1 py-0.5">
                      <input
                        type="checkbox"
                        checked={dependsOn.includes(c.id)}
                        onChange={() => onToggleDependsOn(c.id)}
                        className="accent-blue-500"
                      />
                      <span className="text-xs text-fg-muted truncate">{c.title}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Save / Cancel */}
      <div className="flex gap-2 pt-1 border-t border-line-strong">
        <button
          onClick={onSave}
          disabled={!title.trim()}
          className={`flex-1 bg-gradient-to-r ${isNew ? 'from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700' : 'from-green-500 to-green-600 hover:from-green-600 hover:to-green-700'} text-fg-inverted py-2.5 rounded-lg text-sm font-semibold disabled:opacity-50 transition-all`}
        >
          {saveLabel}
        </button>
        <button onClick={onCancel} className="px-4 py-2.5 bg-surface-raised text-fg-muted rounded-lg text-sm hover:bg-surface-raised transition-colors">
          Cancel
        </button>
      </div>
    </div>
  );
};

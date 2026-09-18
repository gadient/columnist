import React from 'react';
import { Plus, Edit3, Trash2 } from 'lucide-react';
import { KanbanCard } from './KanbanCard';
import { CardForm } from '../form/CardForm';

interface KanbanColumnProps {
  column: any;
  columnId: string;
  cards: any[];
  members: any[];
  allCards: { id: string; title: string }[];
  isDraggedOver: boolean;
  editingCard: any;
  editingCategory: any;
  newCard: any;
  showNewCardForm: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragEnter: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  onDragStartCard: (e: React.DragEvent, cardId: string) => void;
  onEditCard: (card: any) => void;
  onDeleteCard: (cardId: string) => void;
  onDeleteColumn: () => void;
  onToggleCardComplete: (cardId: string) => void;
  onSaveEditCard: () => void;
  onCancelEditCard: () => void;
  onChangeEditCard: (patch: any) => void;
  onShowNewCardForm: () => void;
  onHideNewCardForm: () => void;
  onChangeNewCard: (patch: any) => void;
  onAddNewCard: () => void;
  onStartEditColumn: () => void;
  onSaveColumnTitle: () => void;
  onCancelEditColumn: () => void;
  onChangeColumnTitle: (title: string) => void;
}

const toggle = (arr: any[], val: any) =>
  arr.includes(val) ? arr.filter((x: any) => x !== val) : [...arr, val];

export const KanbanColumn = ({
  column, columnId, cards, members, allCards, isDraggedOver,
  editingCard, editingCategory, newCard, showNewCardForm,
  onDragOver, onDragEnter, onDrop, onDragStartCard,
  onEditCard, onDeleteCard, onDeleteColumn, onToggleCardComplete,
  onSaveEditCard, onCancelEditCard, onChangeEditCard,
  onShowNewCardForm, onHideNewCardForm, onChangeNewCard, onAddNewCard,
  onStartEditColumn, onSaveColumnTitle, onCancelEditColumn, onChangeColumnTitle,
}: KanbanColumnProps) => (
  <div
    className={`flex-shrink-0 w-80 rounded-xl border-2 transition-colors ${
      isDraggedOver ? 'border-blue-500 bg-blue-900/20' : 'border-line bg-surface'
    }`}
    onDragOver={onDragOver}
    onDragEnter={onDragEnter}
    onDrop={onDrop}
  >
    {/* Column header */}
    <div className="p-4 border-b border-line">
      {editingCategory?.id === columnId ? (
        <div className="space-y-2">
          <input
            autoFocus type="text" value={editingCategory.title}
            onChange={e => onChangeColumnTitle(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && editingCategory.title.trim()) onSaveColumnTitle();
              else if (e.key === 'Escape') onCancelEditColumn();
            }}
            className="w-full px-3 py-2 bg-surface-raised border border-line-strong rounded-lg text-sm font-semibold text-fg focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <div className="flex gap-2">
            <button onClick={onSaveColumnTitle} disabled={!editingCategory.title.trim()}
              className="bg-green-600 text-fg-inverted px-3 py-1 rounded-lg text-xs hover:bg-green-700 disabled:opacity-50 transition-colors">Save</button>
            <button onClick={onCancelEditColumn} className="bg-surface-raised text-fg px-3 py-1 rounded-lg text-xs hover:bg-surface-raised transition-colors">Cancel</button>
          </div>
        </div>
      ) : (
        <div className="flex justify-between items-center gap-2">
          <div className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer group" onClick={onStartEditColumn}>
            <h3 className="font-semibold text-fg truncate">{column.title}</h3>
            <Edit3 size={13} className="text-fg-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-sm text-fg-muted bg-surface-raised px-2 py-0.5 rounded-full">{cards.length}</span>
            <button
              onClick={onDeleteColumn}
              aria-label={`Delete column: ${column.title}`}
              title="Delete column"
              className="text-fg-muted hover:text-red-600 dark:hover:text-red-400 transition-colors p-0.5"
            >
              <Trash2 size={14} />
            </button>
          </div>
        </div>
      )}
    </div>

    {/* Cards list */}
    <div className="p-3 space-y-3 max-h-[580px] overflow-y-auto">
      {cards.map(card => {
        if (!card) return null;
        if (editingCard?.id === card.id) {
          return (
            <CardForm
              key={card.id}
              title={editingCard.title}
              description={editingCard.description}
              priority={editingCard.priority}
              dueDate={editingCard.dueDate}
              assignees={editingCard.assignees}
              members={members}
              storyPoints={editingCard.storyPoints ?? null}
              jiraKey={editingCard.jiraKey ?? ''}
              labels={editingCard.labels ?? []}
              blockedBy={editingCard.blockedBy ?? []}
              dependsOn={editingCard.dependsOn ?? []}
              allCards={allCards.filter(c => c.id !== card.id)}
              onChangeTitle={v => onChangeEditCard({ title: v })}
              onChangeDescription={v => onChangeEditCard({ description: v })}
              onChangePriority={v => onChangeEditCard({ priority: v })}
              onChangeDueDate={v => onChangeEditCard({ dueDate: v })}
              onToggleAssignee={id => onChangeEditCard({ assignees: toggle(editingCard.assignees, id) })}
              onChangeStoryPoints={v => onChangeEditCard({ storyPoints: v })}
              onChangeJiraKey={v => onChangeEditCard({ jiraKey: v })}
              onChangeLabels={v => onChangeEditCard({ labels: v })}
              onToggleBlockedBy={id => onChangeEditCard({ blockedBy: toggle(editingCard.blockedBy ?? [], id) })}
              onToggleDependsOn={id => onChangeEditCard({ dependsOn: toggle(editingCard.dependsOn ?? [], id) })}
              onSave={onSaveEditCard}
              onCancel={onCancelEditCard}
              saveLabel="✓ Save Changes"
            />
          );
        }
        return (
          <KanbanCard
            key={card.id}
            card={card}
            members={members}
            onEdit={() => onEditCard(card)}
            onDelete={() => onDeleteCard(card.id)}
            onToggleComplete={() => onToggleCardComplete(card.id)}
            onDragStart={e => onDragStartCard(e, card.id)}
          />
        );
      })}

      {showNewCardForm ? (
        <CardForm
          title={newCard.title}
          description={newCard.description}
          priority={newCard.priority}
          dueDate={newCard.dueDate}
          assignees={newCard.assignees}
          members={members}
          storyPoints={newCard.storyPoints ?? null}
          jiraKey={newCard.jiraKey ?? ''}
          labels={newCard.labels ?? []}
          blockedBy={newCard.blockedBy ?? []}
          dependsOn={newCard.dependsOn ?? []}
          allCards={allCards}
          onChangeTitle={v => onChangeNewCard({ title: v })}
          onChangeDescription={v => onChangeNewCard({ description: v })}
          onChangePriority={v => onChangeNewCard({ priority: v })}
          onChangeDueDate={v => onChangeNewCard({ dueDate: v })}
          onToggleAssignee={id => onChangeNewCard({ assignees: toggle(newCard.assignees, id) })}
          onChangeStoryPoints={v => onChangeNewCard({ storyPoints: v })}
          onChangeJiraKey={v => onChangeNewCard({ jiraKey: v })}
          onChangeLabels={v => onChangeNewCard({ labels: v })}
          onToggleBlockedBy={id => onChangeNewCard({ blockedBy: toggle(newCard.blockedBy ?? [], id) })}
          onToggleDependsOn={id => onChangeNewCard({ dependsOn: toggle(newCard.dependsOn ?? [], id) })}
          onSave={onAddNewCard}
          onCancel={onHideNewCardForm}
          saveLabel="+ Add Card"
          isNew
        />
      ) : (
        <button
          onClick={onShowNewCardForm}
          className="w-full py-2.5 border-2 border-dashed border-line rounded-xl text-fg-muted hover:border-blue-500/60 hover:text-blue-600 dark:hover:text-blue-400 transition-colors flex items-center justify-center gap-2 text-sm"
        >
          <Plus size={16} /> Add Card
        </button>
      )}
    </div>
  </div>
);

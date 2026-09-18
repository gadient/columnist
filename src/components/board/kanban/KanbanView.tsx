import React, { useState } from 'react';
import { KanbanColumn } from './KanbanColumn';
import { DeleteConfirmationModal } from '../../common/DeleteConfirmationModal';

const EMPTY_CARD = {
  title: '', description: '', priority: 'medium', dueDate: '',
  assignees: [] as any[], storyPoints: null as number | null,
  jiraKey: '', labels: [] as string[], blockedBy: [] as string[], dependsOn: [] as string[],
};

export const KanbanView = ({ board, onUpdateBoard, onToggleCardCompletion }) => {
  const [draggedCard, setDraggedCard] = useState<string | null>(null);
  const [draggedOver, setDraggedOver] = useState<string | null>(null);
  const [showNewCardForm, setShowNewCardForm] = useState<string | null>(null);
  const [editingCard, setEditingCard] = useState<any>(null);
  const [editingCategory, setEditingCategory] = useState<any>(null);
  const [newCard, setNewCard] = useState({ ...EMPTY_CARD });
  const [confirmDelete, setConfirmDelete] = useState<{ show: boolean; kind: 'card' | 'column'; id: string; name: string }>({ show: false, kind: 'card', id: '', name: '' });

  const allCards: { id: string; title: string }[] = Object.values(board.cards).map((c: any) => ({ id: c.id, title: c.title }));

  const handleDragStart = (e: React.DragEvent, cardId: string) => {
    setDraggedCard(cardId);
    e.dataTransfer.effectAllowed = 'move';
  };
  const handleDragOver = (e: React.DragEvent) => e.preventDefault();
  const handleDragEnter = (e: React.DragEvent, columnId: string) => { e.preventDefault(); setDraggedOver(columnId); };

  const handleDrop = (e: React.DragEvent, targetColumnId: string) => {
    e.preventDefault();
    if (!draggedCard) return;
    const sourceColumnId = Object.keys(board.columns).find(id => board.columns[id].cardIds.includes(draggedCard));
    if (sourceColumnId === targetColumnId) { setDraggedCard(null); setDraggedOver(null); return; }
    const newColumns = { ...board.columns };
    newColumns[sourceColumnId!] = { ...newColumns[sourceColumnId!], cardIds: newColumns[sourceColumnId!].cardIds.filter(id => id !== draggedCard) };
    newColumns[targetColumnId] = { ...newColumns[targetColumnId], cardIds: [...newColumns[targetColumnId].cardIds, draggedCard] };
    onUpdateBoard({ ...board, columns: newColumns });
    setDraggedCard(null); setDraggedOver(null);
  };

  const addNewCard = (columnId: string) => {
    if (!newCard.title.trim()) return;
    // Collision-proof id. `Date.now()` collides for two cards created in the same millisecond
    // (bulk/scripted creates), and cards.id has a global UNIQUE constraint — a dup 500s and the card
    // is dropped. randomUUID is available in every secure context (HTTPS + localhost).
    const cardId = crypto.randomUUID();
    onUpdateBoard({
      ...board,
      cards: { ...board.cards, [cardId]: { ...newCard, id: cardId, createdAt: new Date().toISOString(), completed: false } },
      columns: { ...board.columns, [columnId]: { ...board.columns[columnId], cardIds: [...board.columns[columnId].cardIds, cardId] } },
    });
    setNewCard({ ...EMPTY_CARD });
    setShowNewCardForm(null);
  };

  const saveEditCard = () => {
    if (!editingCard?.title.trim()) return;
    onUpdateBoard({ ...board, cards: { ...board.cards, [editingCard.id]: { ...editingCard, title: editingCard.title.trim(), description: editingCard.description.trim() } } });
    setEditingCard(null);
  };

  const toggleCardCompletion = (cardId: string) => {
    const next = !board.cards[cardId].completed;
    const updatedCards = { ...board.cards, [cardId]: { ...board.cards[cardId], completed: next } };
    onToggleCardCompletion(cardId, next, { ...board, cards: updatedCards });
  };

  const deleteCard = (cardId: string) => {
    setConfirmDelete({ show: true, kind: 'card', id: cardId, name: board.cards[cardId]?.title ?? '' });
  };

  const deleteColumn = (columnId: string) => {
    const col = board.columns[columnId];
    const count = col?.cardIds?.length ?? 0;
    const name = count > 0 ? `${col.title} (${count} card${count === 1 ? '' : 's'})` : col?.title ?? '';
    setConfirmDelete({ show: true, kind: 'column', id: columnId, name });
  };

  const closeConfirm = () => setConfirmDelete({ show: false, kind: 'card', id: '', name: '' });

  const confirmDeleteCard = () => {
    const newCards = { ...board.cards };
    delete newCards[confirmDelete.id];
    const newColumns = Object.fromEntries(
      Object.entries(board.columns).map(([id, col]: [string, any]) => [id, { ...col, cardIds: col.cardIds.filter((cId: string) => cId !== confirmDelete.id) }])
    );
    onUpdateBoard({ ...board, cards: newCards, columns: newColumns });
    closeConfirm();
  };

  const confirmDeleteColumn = () => {
    const columnId = confirmDelete.id;
    const col = board.columns[columnId];
    // Deleting a column removes its cards too — the whole board mutation flows through onUpdateBoard,
    // which snapshot-syncs (same path as card delete). No dedicated column endpoint needed.
    const newCards = { ...board.cards };
    (col?.cardIds ?? []).forEach((cId: string) => { delete newCards[cId]; });
    const newColumns = { ...board.columns };
    delete newColumns[columnId];
    const newColumnOrder = board.columnOrder.filter((id: string) => id !== columnId);
    onUpdateBoard({ ...board, cards: newCards, columns: newColumns, columnOrder: newColumnOrder });
    closeConfirm();
  };

  const confirmDeleteItem = () => (confirmDelete.kind === 'column' ? confirmDeleteColumn() : confirmDeleteCard());

  const saveColumnTitle = (columnId: string) => {
    if (!editingCategory?.title.trim()) return;
    onUpdateBoard({ ...board, columns: { ...board.columns, [columnId]: { ...board.columns[columnId], title: editingCategory.title.trim() } } });
    setEditingCategory(null);
  };

  return (
    <>
      <DeleteConfirmationModal
        show={confirmDelete.show}
        itemName={confirmDelete.name}
        onCancel={closeConfirm}
        onConfirm={confirmDeleteItem}
      />
      <div className="flex gap-5 overflow-x-auto pb-4">
        {board.columnOrder.map((columnId: string) => {
          const col = board.columns[columnId];
          const cards = col.cardIds.map((id: string) => board.cards[id]).filter(Boolean);
          return (
            <KanbanColumn
              key={columnId}
              column={col}
              columnId={columnId}
              cards={cards}
              members={board.teamMembers}
              allCards={allCards}
              isDraggedOver={draggedOver === columnId}
              editingCard={editingCard}
              editingCategory={editingCategory}
              newCard={newCard}
              showNewCardForm={showNewCardForm === columnId}
              onDragOver={handleDragOver}
              onDragEnter={e => handleDragEnter(e, columnId)}
              onDrop={e => handleDrop(e, columnId)}
              onDragStartCard={handleDragStart}
              onEditCard={card => setEditingCard({ ...card, assignees: [...(card.assignees ?? [])], labels: [...(card.labels ?? [])], blockedBy: [...(card.blockedBy ?? [])], dependsOn: [...(card.dependsOn ?? [])] })}
              onDeleteCard={deleteCard}
              onDeleteColumn={() => deleteColumn(columnId)}
              onToggleCardComplete={toggleCardCompletion}
              onSaveEditCard={saveEditCard}
              onCancelEditCard={() => setEditingCard(null)}
              onChangeEditCard={patch => setEditingCard((prev: any) => ({ ...prev, ...patch }))}
              onShowNewCardForm={() => { setNewCard({ ...EMPTY_CARD }); setShowNewCardForm(columnId); }}
              onHideNewCardForm={() => setShowNewCardForm(null)}
              onChangeNewCard={patch => setNewCard(prev => ({ ...prev, ...patch }))}
              onAddNewCard={() => addNewCard(columnId)}
              onStartEditColumn={() => setEditingCategory({ id: columnId, title: col.title })}
              onSaveColumnTitle={() => saveColumnTitle(columnId)}
              onCancelEditColumn={() => setEditingCategory(null)}
              onChangeColumnTitle={title => setEditingCategory((prev: any) => ({ ...prev, title }))}
            />
          );
        })}
      </div>
    </>
  );
};

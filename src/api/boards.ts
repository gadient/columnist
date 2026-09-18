import { apiRequest } from './client';

export const apiListBoards = async () => apiRequest('/boards');

export const apiGetBoard = async (boardId) => apiRequest(`/boards/${boardId}`);

export const apiCreateBoard = async (payload) =>
  apiRequest('/boards', { method: 'POST', body: JSON.stringify(payload) });

export const apiDeleteBoard = async (boardId) =>
  apiRequest(`/boards/${boardId}`, { method: 'DELETE' });

export const apiSyncBoard = async (boardId, boardSnapshot) =>
  apiRequest(`/boards/${boardId}/snapshot`, {
    method: 'PUT',
    body: JSON.stringify(boardSnapshot)
  });

export const apiToggleCardComplete = async (boardId, cardId, completed) =>
  apiRequest(`/boards/${boardId}/cards/${cardId}/toggle-complete`, {
    method: 'POST',
    body: JSON.stringify({ completed })
  });

export const toBoardMap = (boardsList) => {
  return boardsList.reduce((acc, board) => {
    acc[board.id] = board;
    return acc;
  }, {});
};

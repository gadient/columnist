import { apiRequest } from './client';

export const apiGetWorkload = (boardId?: string) =>
  apiRequest(`/analytics/workload${boardId ? `?board_id=${encodeURIComponent(boardId)}` : ''}`);

export const apiGetFlowMetrics = (boardId?: string) =>
  apiRequest(`/analytics/flow${boardId ? `?board_id=${encodeURIComponent(boardId)}` : ''}`);

export const apiGetDependencies = (boardId?: string) =>
  apiRequest(`/analytics/dependencies${boardId ? `?board_id=${encodeURIComponent(boardId)}` : ''}`);

export const apiGetIntelligence = (boardId?: string): Promise<{
  narrative: string;
  facts: Record<string, any[]>;
  generated_at: string;
  has_ai: boolean;
}> =>
  apiRequest('/analytics/intelligence', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ board_id: boardId ?? null }),
  });

export const apiListTeams = () => apiRequest('/teams');

export const apiCreateTeam = (payload: { name: string; color: string; jiraPrefix?: string; description?: string }) =>
  apiRequest('/teams', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

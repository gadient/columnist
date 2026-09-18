import { apiRequest } from './client';

export const apiListWorkspaces = async () => apiRequest('/workspaces');

export const apiCreateWorkspace = async (name) =>
  apiRequest('/workspaces', { method: 'POST', body: JSON.stringify({ name }) });

export const apiRenameWorkspace = async (id, name) =>
  apiRequest(`/workspaces/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) });

export const apiDeleteWorkspace = async (id) =>
  apiRequest(`/workspaces/${id}`, { method: 'DELETE' });

export const apiListWorkspaceBoards = async (workspaceId) =>
  apiRequest(`/workspaces/${workspaceId}/boards`);

import { apiRequest } from './client';

export type FeedbackCategory = 'chat' | 'notes' | 'general' | 'bug';

export type FeedbackView = {
  id: string;
  instanceId: string | null;
  userId: string | null;
  userEmail: string | null;
  category: string;
  rating: number | null;
  message: string;
  page: string | null;
  createdAt: string;
};

export const apiSubmitFeedback = (input: {
  category: FeedbackCategory;
  message: string;
  rating?: number | null;
  page?: string | null;
}): Promise<FeedbackView> =>
  apiRequest('/feedback', { method: 'POST', body: JSON.stringify(input) });

/**
 * Read the inbox. Restricted to the Instance's primary user server-side, which answers 404 for
 * anyone else — so callers must treat a failure as "not for you" rather than as an error worth
 * showing.
 */
export const apiListFeedback = (): Promise<FeedbackView[]> => apiRequest('/feedback');

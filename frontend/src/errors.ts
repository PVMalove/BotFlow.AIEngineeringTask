import { ApiError } from './api';
import type { Messages } from './locale';

export function errorMessage(error: unknown, messages: Messages) {
  if (!(error instanceof ApiError)) return messages.requestFailed;
  if (error.status === 0) return messages.networkError;
  if (error.status === 401) return messages.invalidCode;
  if (error.status === 403) return messages.accessDenied;
  if (error.status === 429) return messages.rateLimited;
  if (error.status >= 500) return messages.serviceUnavailable;
  return messages.requestFailed;
}

export type SaveFailure =
  | 'conflict'
  | 'unknown'
  | 'forbidden'
  | 'missing'
  | 'unauthorized'
  | 'rateLimited'
  | 'failed';

// One decision for a rejected status change: what the screen and cache do next.
export function classifySaveError(error: unknown): SaveFailure {
  if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500)
    return 'unknown';
  if (error.status === 409) return 'conflict';
  if (error.status === 403) return 'forbidden';
  if (error.status === 404) return 'missing';
  if (error.status === 401) return 'unauthorized';
  if (error.status === 429) return 'rateLimited';
  return 'failed';
}

export function isAccessDenied(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

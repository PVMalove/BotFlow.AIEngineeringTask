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

export function isAccessDenied(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.status);
}

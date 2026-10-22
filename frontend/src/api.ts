export interface Member {
  id: string;
  telegram_id: number;
  role: 'admin' | 'user';
  status: string;
}

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super('API request failed');
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch {
    throw new ApiError(0);
  }
  if (!response.ok) throw new ApiError(response.status);
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}

export function getCurrentMember(): Promise<Member> {
  return request('/api/v1/users/me');
}

export function exchangeCode(code: string): Promise<Member> {
  return request('/api/v1/auth/exchange', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}

export function logout(): Promise<void> {
  return request('/api/v1/auth/logout', { method: 'POST' });
}

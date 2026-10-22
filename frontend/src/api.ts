export interface Member {
  id: string;
  telegram_id: number;
  role: 'admin' | 'user';
  status: string;
}

export interface AdminMember extends Member {
  status: 'pending' | 'approved' | 'rejected' | 'blocked';
  display_name: string | null;
  access_version: number;
  created_at: string;
  status_changed_at: string | null;
}

export type AccessAction = 'approve' | 'reject' | 'block' | 'unblock';
export type AccessList = 'pending' | 'rejected' | 'members';
export const accessPageSize = 20;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code?: string,
  ) {
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
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? body.detail
        : undefined;
    const code =
      typeof detail === 'string' &&
      [
        'version_conflict',
        'invalid_transition',
        'cannot_block_self',
        'last_admin',
      ].includes(detail)
        ? detail
        : undefined;
    throw new ApiError(response.status, code);
  }
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

export function listAccess(
  list: AccessList,
  page: number,
): Promise<AdminMember[]> {
  const resource = list === 'members' ? 'members' : 'join-requests';
  const params = new URLSearchParams({
    limit: String(accessPageSize),
    offset: String(page * accessPageSize),
  });
  if (list !== 'members') params.set('status', list);
  return request(`/api/v1/admin/${resource}?${params}`);
}

export function decideAccess(
  member: AdminMember,
  action: AccessAction,
): Promise<{ member: AdminMember; changed: boolean }> {
  return request(`/api/v1/admin/members/${member.id}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_version: member.access_version }),
  });
}

import { expect, test } from 'vitest';

import { ApiError } from './api';
import { classifySaveError, isAccessDenied } from './errors';

test.each([
  [new ApiError(409, 'version_conflict'), 'conflict'],
  [new ApiError(0), 'unknown'],
  [new ApiError(500), 'unknown'],
  [new ApiError(503), 'unknown'],
  [new TypeError('boom'), 'unknown'],
  [new ApiError(403), 'forbidden'],
  [new ApiError(404), 'missing'],
  [new ApiError(401), 'unauthorized'],
  [new ApiError(429), 'rateLimited'],
  [new ApiError(400), 'failed'],
  [new ApiError(422), 'failed'],
])('a rejected status change %j is classified as %s', (error, expected) => {
  expect(classifySaveError(error)).toBe(expected);
});

test('only 401 and 403 responses mean access is denied', () => {
  expect(isAccessDenied(new ApiError(401))).toBe(true);
  expect(isAccessDenied(new ApiError(403))).toBe(true);
  expect(isAccessDenied(new ApiError(409))).toBe(false);
  expect(isAccessDenied(new Error('401'))).toBe(false);
  expect(isAccessDenied(null)).toBe(false);
});

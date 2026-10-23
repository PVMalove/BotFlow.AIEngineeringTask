import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { startBoardRealtime } from './boardRealtime';
import type { BoardConnection } from './boardRealtime';

export function useBoardRealtime(memberId: string) {
  const client = useQueryClient();
  const [connection, setConnection] = useState<BoardConnection>('connecting');
  const realtime = useRef<ReturnType<typeof startBoardRealtime> | null>(null);
  useEffect(() => {
    const sync = startBoardRealtime(client, memberId, setConnection);
    realtime.current = sync;
    return () => {
      sync.stop();
      realtime.current = null;
    };
  }, [client, memberId]);
  return { connection, refresh: () => realtime.current?.refresh() };
}

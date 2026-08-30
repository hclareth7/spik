import { useCallback, useEffect, useRef } from 'react';

// Thin EventSource wrapper. Gives imperative open/close control (the mic-level meter and
// the analyze progress stream are both started/stopped by user actions), parses each
// `data:` frame as JSON, and always tears the connection down on unmount.

export interface SSEHandlers<T> {
  onMessage: (data: T) => void;
  onError?: () => void;
}

export interface SSEController<T> {
  open: (url: string, handlers: SSEHandlers<T>) => void;
  close: () => void;
  isOpen: () => boolean;
}

export function useSSE<T = unknown>(): SSEController<T> {
  const ref = useRef<EventSource | null>(null);

  const close = useCallback(() => {
    if (ref.current) {
      ref.current.close();
      ref.current = null;
    }
  }, []);

  const open = useCallback(
    (url: string, handlers: SSEHandlers<T>) => {
      close();
      const es = new EventSource(url);
      es.onmessage = (ev: MessageEvent<string>) => {
        try {
          handlers.onMessage(JSON.parse(ev.data) as T);
        } catch {
          /* ignore malformed frame */
        }
      };
      es.onerror = () => handlers.onError?.();
      ref.current = es;
    },
    [close],
  );

  // Tear down any live stream when the owning component unmounts.
  useEffect(() => close, [close]);

  const isOpen = useCallback(() => ref.current !== null, []);

  return { open, close, isOpen };
}

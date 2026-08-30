import { useEffect, useState } from 'react';

import { getConfig } from '../api/client';
import type { AppConfig } from '../api/types';

// Reads GET /api/config once on mount. `config` is null while loading. On failure it
// falls back to a local-mode default (matches app.js applyMode()'s try/catch behavior).
export function useMode(): { config: AppConfig | null } {
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    let alive = true;
    getConfig()
      .then((c) => alive && setConfig(c))
      .catch(() => alive && setConfig({ mode: 'local', noise: true, vcam: true }));
    return () => {
      alive = false;
    };
  }, []);

  return { config };
}

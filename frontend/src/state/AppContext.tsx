import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { getDevices, getPrefs, listProjects, setPrefs } from '../api/client';
import type { Camera, Mode, Prefs, Project, Source } from '../api/types';

// Shared cross-tab state, matching what app.js kept in the DOM:
//  - the camera/mic selection lived in the Checker tab's <select>s but the Record tab
//    read their .value, so the selection is shared here.
//  - the projects list is shared between the Record tab (where to save) and the
//    Feedback tab's history filter.
interface AppState {
  cameras: Camera[];
  sources: Source[];
  camera: string;
  mic: string;
  selectCamera: (device: string) => void;
  selectMic: (name: string) => void;
  refreshDevices: () => Promise<void>;
  devicesError: string | null;
  projects: Project[];
  refreshProjects: () => Promise<void>;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useApp must be used within <AppProvider>');
  return ctx;
}

export function AppProvider({ mode, children }: { mode: Mode; children: ReactNode }) {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [camera, setCamera] = useState('');
  const [mic, setMic] = useState('');
  const [devicesError, setDevicesError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);

  // Refs so prefs are persisted with the latest counterpart value (savePrefs posts both).
  const cameraRef = useRef('');
  const micRef = useRef('');
  cameraRef.current = camera;
  micRef.current = mic;

  const savePrefs = useCallback((next: { camera: string; mic: string }) => {
    // Non-critical: swallow errors exactly like app.js savePrefs().
    void setPrefs(next).catch(() => undefined);
  }, []);

  const selectCamera = useCallback(
    (device: string) => {
      setCamera(device);
      savePrefs({ camera: device, mic: micRef.current });
    },
    [savePrefs],
  );

  const selectMic = useCallback(
    (name: string) => {
      setMic(name);
      savePrefs({ camera: cameraRef.current, mic: name });
    },
    [savePrefs],
  );

  const refreshDevices = useCallback(async () => {
    try {
      const { cameras: cams, sources: srcs } = await getDevices();
      setCameras(cams);
      setSources(srcs);
      setDevicesError(null);
      const prefs = await getPrefs().catch((): Prefs => ({}));
      // Apply remembered devices if still present; otherwise keep a valid current
      // selection, else fall back to the first option (browser <select> default).
      setCamera((prev) => {
        if (prefs.camera && cams.some((c) => c.device === prefs.camera)) return prefs.camera;
        if (prev && cams.some((c) => c.device === prev)) return prev;
        return cams[0]?.device ?? '';
      });
      setMic((prev) => {
        if (prefs.mic && srcs.some((s) => s.name === prefs.mic)) return prefs.mic;
        if (prev && srcs.some((s) => s.name === prev)) return prev;
        return srcs[0]?.name ?? '';
      });
    } catch (e) {
      setDevicesError((e as Error).message);
    }
  }, []);

  const refreshProjects = useCallback(async () => {
    // Non-critical: on failure the pickers just stay as-is (app.js loadProjects()).
    try {
      const { projects: ps } = await listProjects();
      setProjects(ps);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (mode !== 'server') void refreshDevices();
    void refreshProjects();
  }, [mode, refreshDevices, refreshProjects]);

  const value: AppState = {
    cameras,
    sources,
    camera,
    mic,
    selectCamera,
    selectMic,
    refreshDevices,
    devicesError,
    projects,
    refreshProjects,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

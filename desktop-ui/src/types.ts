import type { Language } from './i18n'
export type Theme = 'system' | 'dark' | 'light'
export type Phase = 'idle' | 'scanning' | 'ready' | 'cleaning' | 'completed' | 'cancelled' | 'error'
export interface Category {
  id: string; title: string; description: string; selected: boolean; available: boolean;
  reason: string | null; files: number; bytes: number; skipped: number;
  group?: string; recommended?: boolean; native_action?: string | null;
  actions?: number; size_known?: boolean; analysis_error?: boolean; analysis_pending?: boolean
}
export interface CleanupActionResult {
  category_id: string; title: string; status: 'completed' | 'failed' | 'skipped' | 'deferred';
  freed_bytes: number | null; detail: string | null
}
export interface CleanupResult {
  removed: number; freed_bytes: number; skipped: number; failed: number; cancelled: boolean;
  actions?: CleanupActionResult[]; actions_completed?: number; actions_failed?: number; actions_deferred?: number
}
export interface CleanerState {
  version: string; phase: Phase; categories: Category[]; scan_id: string | null; scanned_at: string | null;
  progress: { checked: number; files: number; bytes: number; current: string | null; message?: string | null };
  result: CleanupResult | null; error: string | null; theme?: Theme; language?: Language
  disk?: { drive: string; free: number; total: number } | null; administrator?: boolean; update?: UpdateState | null; tools?: ToolsState
  maintenance?: { action_id: string; operation: 'analyze' | 'cleanup'; phase: 'running' | 'finishing'; elapsed_seconds: number } | null
}
export interface UpdateState {
  status: 'unsupported' | 'idle' | 'checking' | 'current' | 'available' | 'downloading' | 'ready' | 'failed'
  version: string | null; progress: number | null; error: string | null
}
export type ConnectionTarget = 'cloudflare' | 'google'
export interface UpdatePackage { id: string; name: string; version: string; available: string; source: string; status: 'pending' | 'updating' | 'updated' | 'failed' }
export interface ToolsState {
  administrator: boolean
  dns: { status: 'idle' | 'running' | 'done' | 'failed'; error: string | null }
  connection: { status: 'idle' | 'running' | 'done' | 'failed'; target: string; samples: (number | null)[]; error: string | null
    summary: { average: number | null; jitter: number | null; best: number | null; worst: number | null; loss: number; verdict: 'stable' | 'unstable' | 'losing' | 'offline' | null } | null }
  updates: { status: 'idle' | 'checking' | 'ready' | 'updating' | 'done' | 'failed'; packages: UpdatePackage[]; current: string | null; error: string | null }
  repair: { status: 'idle' | 'running' | 'done' | 'failed'; phase: 'dism' | 'sfc' | null; progress: number | null; error: string | null
    result: { verdict: 'clean' | 'repaired' | 'unrepaired' | 'failed'; repaired: number; unrepaired: number } | null }
  disks: { status: 'idle' | 'running' | 'done' | 'failed'; error: string | null
    items: { name: string; media: string; bus: string; health: 'healthy' | 'warning' | 'unhealthy' | 'unknown'; size: number | null; temperature: number | null; wear: number | null }[] }
  startup: { status: 'idle' | 'done' | 'failed'; error: string | null; items: { id: string; name: string; program: string; scope: 'user' | 'machine'; enabled: boolean; fixed?: boolean }[] }
}
export interface FileItem { name: string; path: string; bytes: number }
export type StateResponse = { ok: true; state: CleanerState } | { ok: false; error: string }
export type FilesResponse = { ok: true; items: FileItem[]; total: number; next_offset: number | null } | { ok: false; error: string }
export interface CleanerApi {
  get_state(): Promise<StateResponse>;
  set_selection(ids: string[]): Promise<StateResponse>;
  scan(): Promise<StateResponse>;
  cancel(): Promise<StateResponse>;
  clean(scanId: string, ids: string[]): Promise<StateResponse>;
  open_search_settings(): Promise<StateResponse>;
  get_files(scanId: string, categoryId: string, offset: number): Promise<FilesResponse>;
  set_theme(theme: Theme): Promise<StateResponse>;
  set_language(language: Language): Promise<StateResponse>;
  restart_as_administrator(): Promise<StateResponse>;
  download_update(): Promise<StateResponse>;
  flush_dns(): Promise<StateResponse>;
  test_connection(target: ConnectionTarget): Promise<StateResponse>;
  stop_connection(): Promise<StateResponse>;
  check_disks(): Promise<StateResponse>;
  list_startup(): Promise<StateResponse>;
  set_startup(id: string, enabled: boolean): Promise<StateResponse>;
  check_updates(): Promise<StateResponse>;
  install_updates(ids: string[]): Promise<StateResponse>;
  stop_updates(): Promise<StateResponse>;
  repair_windows(): Promise<StateResponse>;
  install_update(): Promise<StateResponse>;
  dismiss_update(): Promise<StateResponse>;
  open_update_page(): Promise<StateResponse>;
  window_action?(action: WindowAction): Promise<{ ok: true } | { ok: false; error: string }>;
}
export type ResizeEdge = 'top' | 'bottom' | 'left' | 'right' | 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'
export type WindowAction = 'drag' | `resize-${ResizeEdge}` | 'minimize' | 'maximize' | 'close'
declare global { interface Window { pywebview?: { api: CleanerApi } } }
export const initialState: CleanerState = {
  version: '', phase: 'idle', categories: [], scan_id: null, scanned_at: null,
  progress: { checked: 0, files: 0, bytes: 0, current: null }, result: null, error: null,
}

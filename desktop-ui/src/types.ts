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
  result: CleanupResult | null; error: string | null
  maintenance?: { action_id: string; operation: 'analyze' | 'cleanup'; phase: 'running' | 'finishing'; elapsed_seconds: number } | null
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
}
declare global { interface Window { pywebview?: { api: CleanerApi } } }
export const initialState: CleanerState = {
  version: '', phase: 'idle', categories: [], scan_id: null, scanned_at: null,
  progress: { checked: 0, files: 0, bytes: 0, current: null }, result: null, error: null,
}

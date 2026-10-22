export type Phase = 'idle' | 'scanning' | 'ready' | 'cleaning' | 'completed' | 'cancelled' | 'error'
export interface Category {
  id: string; title: string; description: string; selected: boolean; available: boolean;
  reason: string | null; files: number; bytes: number; skipped: number
}
export interface CleanupResult {
  removed: number; freed_bytes: number; skipped: number; failed: number; cancelled: boolean
}
export interface CleanerState {
  version: string; phase: Phase; categories: Category[]; scan_id: string | null; scanned_at: string | null;
  progress: { checked: number; files: number; bytes: number; current: string | null };
  result: CleanupResult | null; error: string | null
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
  get_files(scanId: string, categoryId: string, offset: number): Promise<FilesResponse>;
}
declare global { interface Window { pywebview?: { api: CleanerApi } } }
export const initialState: CleanerState = {
  version: '', phase: 'idle', categories: [], scan_id: null, scanned_at: null,
  progress: { checked: 0, files: 0, bytes: 0, current: null }, result: null, error: null,
}

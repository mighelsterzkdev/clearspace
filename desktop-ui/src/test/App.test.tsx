import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import type { CleanerApi, CleanerState, FilesResponse, StateResponse } from '../types'

function initial(): CleanerState {
  return {
    version: '0.1.0', phase: 'idle', scan_id: null, scanned_at: null,
    categories: [
      { id: 'user_temp', title: 'Temporary files', description: 'User temporary files older than 7 days.', available: true, selected: true, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'chrome_cache', title: 'Chrome cache', description: 'Cached web content. Browser sign-ins are kept.', available: true, selected: false, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'edge_cache', title: 'Edge cache', description: 'Cached web content.', available: false, selected: false, reason: 'Close Microsoft Edge to analyze this cache.', bytes: 0, files: 0, skipped: 0 },
      { id: 'firefox_cache', title: 'Firefox cache', description: 'Cached web content.', available: false, selected: false, reason: 'Not installed.', bytes: 0, files: 0, skipped: 0 },
      { id: 'pip_cache', title: 'Python package cache', description: 'Cached package downloads older than 7 days.', available: true, selected: false, reason: null, bytes: 0, files: 0, skipped: 0 },
    ],
    progress: { checked: 0, files: 0, bytes: 0, current: null }, result: null, error: null,
  }
}

function bridge() {
  let state = initial()
  const snapshot = (): StateResponse => ({ ok: true, state: structuredClone(state) })
  const files = [
    { name: 'old-cache.tmp', path: 'C:\\Sandbox\\Temp\\old-cache.tmp', bytes: 1024 },
    { name: 'installer.tmp', path: 'C:\\Sandbox\\Temp\\installer.tmp', bytes: 512 },
    { name: 'session.tmp', path: 'C:\\Sandbox\\Temp\\session.tmp', bytes: 512 },
  ]
  const api = {
    get_state: vi.fn(async () => snapshot()),
    set_selection: vi.fn(async (ids: string[]) => {
      state = { ...state, phase: 'idle', scan_id: null, result: null, categories: state.categories.map(category => ({ ...category, selected: ids.includes(category.id), bytes: 0, files: 0 })) }
      return snapshot()
    }),
    scan: vi.fn(async () => {
      state = { ...state, phase: 'ready', scan_id: 'scan-1', scanned_at: '2026-10-22T12:00:00Z', categories: state.categories.map(category => ({ ...category, files: category.selected ? 3 : 0, bytes: category.selected ? 2048 : 0 })) }
      return snapshot()
    }),
    cancel: vi.fn(async () => { state = { ...state, phase: 'cancelled', scan_id: null }; return snapshot() }),
    clean: vi.fn(async (_scanId: string, _ids: string[]) => {
      state = { ...state, phase: 'completed', scan_id: null, result: { removed: 2, freed_bytes: 1536, skipped: 1, failed: 0, cancelled: false } }
      return snapshot()
    }),
    get_files: vi.fn(async (_scanId: string, _categoryId: string, offset: number): Promise<FilesResponse> => ({ ok: true, items: files.slice(offset, offset + 2), total: files.length, next_offset: offset === 0 ? 2 : null })),
  } satisfies CleanerApi
  window.pywebview = { api }
  return { api, snapshot, update: (next: Partial<CleanerState>) => { state = { ...state, ...next } } }
}

async function connected() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled())
}

describe('cleanup flow', () => {
  it('starts with the selected profile and requires analysis before cleaning', async () => {
    const { api } = bridge()
    render(<App />)
    await connected()
    expect(screen.getByRole('checkbox', { name: /Temporary files/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Chrome cache/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Edge cache/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
    expect(api.scan).not.toHaveBeenCalled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('analyzes without deleting and requires a specific confirmation before cleaning', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove 2 KiB?' })
    expect(within(dialog).getByText('3 files from the selected categories will be permanently deleted.')).toBeVisible()
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Delete selected files' }))
    await waitFor(() => expect(api.clean).toHaveBeenCalledExactlyOnceWith('scan-1', ['user_temp']))
    expect(await screen.findByRole('heading', { name: 'Cleanup complete' })).toBeVisible()
    expect(screen.getByText(/removed · 2 files · 1 skipped/)).toBeVisible()
    expect(screen.getByText('Total size of deleted files. Disk free space may differ.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
  })

  it('cancels the confirmation with Escape without deleting files', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await user.click(await screen.findByRole('button', { name: 'Clean selected' }))
    expect(screen.getByRole('dialog')).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('invalidates the previous analysis when the selection changes', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    await user.click(screen.getByRole('checkbox', { name: /Chrome cache/ }))
    expect(api.set_selection).toHaveBeenCalledExactlyOnceWith(['user_temp', 'chrome_cache'])
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'View files for Temporary files' })).toBeDisabled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('supports selecting categories with the keyboard', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    screen.getByRole('checkbox', { name: /Chrome cache/ }).focus()
    await user.keyboard(' ')
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Chrome cache/ })).toBeChecked())
    expect(api.set_selection).toHaveBeenCalledExactlyOnceWith(['user_temp', 'chrome_cache'])
  })

  it('disables Analyze when no categories are selected', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('checkbox', { name: /Temporary files/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze' })).toBeDisabled())
    expect(api.set_selection).toHaveBeenCalledExactlyOnceWith([])
  })

  it('can stop an analysis and requires another complete analysis', async () => {
    const user = userEvent.setup()
    const { api, update, snapshot } = bridge()
    api.scan.mockImplementationOnce(async () => { update({ phase: 'scanning' }); return snapshot() })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await user.click(await screen.findByRole('button', { name: 'Stop' }))
    expect(api.cancel).toHaveBeenCalledOnce()
    expect(await screen.findByText('Analysis stopped. Run Analyze again to prepare a complete file list.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
  })

  it('shows an empty analysis without enabling deletion', async () => {
    const user = userEvent.setup()
    const { api, snapshot, update } = bridge()
    api.scan.mockImplementationOnce(async () => { update({ phase: 'ready', scan_id: 'empty-scan' }); return snapshot() })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByText('Nothing to clean in the selected categories')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
  })

  it('reports partial cleanup, skips, and failures without claiming exact free disk space', async () => {
    const { update } = bridge()
    update({ phase: 'cancelled', result: { removed: 1, freed_bytes: 1024, skipped: 2, failed: 1, cancelled: true } })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Cleanup stopped' })).toBeVisible()
    expect(screen.getByText(/removed · 1 file · 2 skipped · 1 could not be removed/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
  })

  it('shows readable action errors and allows another analysis', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    api.scan.mockResolvedValueOnce({ ok: false, error: 'The previous analysis expired. Analyze again.' })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The previous analysis expired. Analyze again.')
    await user.click(screen.getByRole('button', { name: 'Dismiss error' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled()
  })

  it('blocks actions and avoids leaking exception details after a disconnected action', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    api.scan.mockRejectedValueOnce(new Error('private backend traceback'))
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The desktop connection was interrupted.')
    expect(screen.queryByText(/private backend traceback/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeDisabled()
  })

  it('waits for a pending snapshot before mutating the selection', async () => {
    const user = userEvent.setup()
    const { api, snapshot } = bridge()
    render(<App />)
    await connected()
    let resolvePoll: ((response: StateResponse) => void) | undefined
    api.get_state.mockImplementationOnce(() => new Promise(resolve => { resolvePoll = resolve }))
    await waitFor(() => expect(resolvePoll).toBeDefined(), { timeout: 1500 })
    await user.click(screen.getByRole('checkbox', { name: /Chrome cache/ }))
    expect(api.set_selection).not.toHaveBeenCalled()
    await act(async () => { resolvePoll!(snapshot()) })
    await waitFor(() => expect(api.set_selection).toHaveBeenCalledExactlyOnceWith(['user_temp', 'chrome_cache']))
    expect(screen.getByRole('checkbox', { name: /Chrome cache/ })).toBeChecked()
  })
})

describe('file review', () => {
  it('loads a scanned category and paginates without triggering deletion', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await user.click(screen.getByRole('button', { name: 'View files for Temporary files' }))
    const dialog = screen.getByRole('dialog', { name: 'Temporary files' })
    expect(await within(dialog).findByText('old-cache.tmp')).toBeVisible()
    expect(api.get_files).toHaveBeenNthCalledWith(1, 'scan-1', 'user_temp', 0)
    await user.click(within(dialog).getByRole('button', { name: 'Load more' }))
    expect(await within(dialog).findByText('session.tmp')).toBeVisible()
    expect(api.get_files).toHaveBeenNthCalledWith(2, 'scan-1', 'user_temp', 2)
    expect(within(dialog).queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('ignores stale file results after closing the file dialog', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    let resolveFiles: ((response: FilesResponse) => void) | undefined
    api.get_files.mockImplementationOnce(() => new Promise(resolve => { resolveFiles = resolve }))
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await user.click(screen.getByRole('button', { name: 'View files for Temporary files' }))
    expect(await screen.findByText('Loading files…')).toBeVisible()
    await user.keyboard('{Escape}')
    await act(async () => { resolveFiles!({ ok: true, items: [{ name: 'stale.tmp', path: 'C:\\Sandbox\\stale.tmp', bytes: 10 }], total: 1, next_offset: null }) })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText('stale.tmp')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'View files for Temporary files' }))
    expect(await screen.findByText('old-cache.tmp')).toBeVisible()
    expect(screen.queryByText('stale.tmp')).not.toBeInTheDocument()
  })
})

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import type { Category, CleanerApi, CleanerState, FilesResponse, StateResponse, ToolsState, WindowAction } from '../types'

function initial(): CleanerState {
  return {
    version: '0.5.0', phase: 'idle', scan_id: null, scanned_at: null,
    categories: [
      { id: 'recycle_bin', title: 'Recycle Bin', description: 'Deleted files and folders.', group: 'Windows', native_action: 'empty_recycle_bin', recommended: false, available: true, selected: false, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'user_temp', title: 'Temporary files', description: 'User temporary files.', group: 'Windows', recommended: true, available: true, selected: true, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'chrome_cache', title: 'Chrome cache', description: 'Cached web content. Browser sign-ins are kept.', group: 'Browsers', recommended: false, available: true, selected: false, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'edge_cache', title: 'Edge cache', description: 'Cached web content.', group: 'Browsers', recommended: false, available: false, selected: false, reason: 'Close Microsoft Edge to analyze this cache.', bytes: 0, files: 0, skipped: 0 },
      { id: 'firefox_cache', title: 'Firefox cache', description: 'Cached web content.', group: 'Browsers', recommended: false, available: false, selected: false, reason: 'Not installed.', bytes: 0, files: 0, skipped: 0 },
      { id: 'pip_cache', title: 'Python package cache', description: 'Cached package downloads.', group: 'Developer tools', recommended: true, available: true, selected: false, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'windows_logs', title: 'Windows diagnostic logs', description: 'Application diagnostic reports.', group: 'Windows', recommended: true, available: true, selected: false, reason: null, bytes: 0, files: 0, skipped: 0 },
      { id: 'discord_cache', title: 'Discord cache', description: 'Cached application files.', group: 'Applications', recommended: false, available: true, selected: false, reason: 'Some cache folders could not be inspected.', bytes: 0, files: 0, skipped: 0 },
    ],
    progress: { checked: 0, files: 0, bytes: 0, current: null }, result: null, error: null,
  }
}

function toolsState(): ToolsState {
  return {
    administrator: false,
    dns: { status: 'idle', error: null },
    connection: { status: 'idle', target: 'cloudflare', samples: [], summary: null, error: null },
    updates: { status: 'idle', packages: [], current: null, error: null },
    repair: { status: 'idle', phase: null, progress: null, result: null, error: null },
    disks: { status: 'idle', items: [], error: null },
    startup: { status: 'idle', items: [], error: null },
  }
}

function nativeCategory(id: string, title: string, sizeKnown = true): Category {
  return { id, title, native_action: id, group: 'Windows', description: 'Native Windows cleanup.', available: true,
    selected: false, reason: null, bytes: 0, files: 0, skipped: 0, actions: 0, size_known: sizeKnown }
}

function bridge(extraCategories: Category[] = []) {
  let state = initial()
  state.categories.push(...extraCategories)
  state.tools = toolsState()
  const tools = () => state.tools!
  const snapshot = (): StateResponse => ({ ok: true, state: structuredClone(state) })
  const files = [
    { name: 'old-cache.tmp', path: 'C:\\Sandbox\\Temp\\old-cache.tmp', bytes: 1024 },
    { name: 'installer.tmp', path: 'C:\\Sandbox\\Temp\\installer.tmp', bytes: 512 },
    { name: 'session.tmp', path: 'C:\\Sandbox\\Temp\\session.tmp', bytes: 512 },
  ]
  const api = {
    get_state: vi.fn(async () => snapshot()),
    set_selection: vi.fn(async (ids: string[]) => {
      state = { ...state, phase: 'idle', scan_id: null, result: null, categories: state.categories.map(category => ({ ...category, selected: ids.includes(category.id), bytes: 0, files: 0, actions: 0 })) }
      return snapshot()
    }),
    scan: vi.fn(async () => {
      state = { ...state, phase: 'ready', scan_id: 'scan-1', scanned_at: '2026-10-22T12:00:00Z', categories: state.categories.map(category => {
        const native = Boolean(category.native_action) && category.native_action !== 'empty_recycle_bin'
        return { ...category, files: category.selected && !native ? 3 : 0, bytes: category.selected && !native ? 2048 : 0, actions: category.selected && native ? 1 : 0 }
      }) }
      return snapshot()
    }),
    cancel: vi.fn(async () => { state = { ...state, phase: 'cancelled', scan_id: null }; return snapshot() }),
    clean: vi.fn(async (_scanId: string, _ids: string[]) => {
      const native = state.categories.filter(category => category.selected && category.native_action && category.native_action !== 'empty_recycle_bin')
      const filesSelected = state.categories.some(category => category.selected && category.files > 0)
      state = { ...state, phase: 'completed', scan_id: null, result: { removed: filesSelected ? 2 : 0, freed_bytes: filesSelected ? 1536 : 0,
        skipped: filesSelected ? 1 : 0, failed: 0, cancelled: false, actions_completed: native.length, actions_failed: 0,
        actions: native.map(category => ({ category_id: category.id, title: category.title, status: 'completed', freed_bytes: category.size_known === false ? null : 0, detail: null })) } }
      return snapshot()
    }),
    open_search_settings: vi.fn(async () => snapshot()),
    set_theme: vi.fn(async (theme: 'system' | 'dark' | 'light') => { state = { ...state, theme }; return snapshot() }),
    set_language: vi.fn(async (language: 'system' | 'pt' | 'en') => { state = { ...state, language }; return snapshot() }),
    restart_as_administrator: vi.fn(async () => snapshot()),
    download_update: vi.fn(async () => { state = { ...state, update: { status: 'ready', version: '0.6.0', progress: 1, error: null } }; return snapshot() }),
    install_update: vi.fn(async () => snapshot()),
    dismiss_update: vi.fn(async () => { state = { ...state, update: { status: 'idle', version: null, progress: null, error: null } }; return snapshot() }),
    open_update_page: vi.fn(async () => snapshot()),
    flush_dns: vi.fn(async () => { tools().dns = { status: 'done', error: null }; return snapshot() }),
    test_connection: vi.fn(async (_target: 'cloudflare' | 'google') => {
      tools().connection = { status: 'done', target: 'cloudflare', samples: [11, 12, null, 11], error: null,
        summary: { average: 11.3, jitter: 0.7, best: 11, worst: 12, loss: 25, verdict: 'losing' } }
      return snapshot()
    }),
    stop_connection: vi.fn(async () => snapshot()),
    check_disks: vi.fn(async () => { tools().disks = { status: 'done', error: null, items: [{ name: 'KINGSTON SA400', media: 'SSD', bus: 'SATA', health: 'healthy', size: 960197124096, temperature: null, wear: null }] }; return snapshot() }),
    list_startup: vi.fn(async () => {
      tools().startup = { status: 'done', error: null, items: [
        { id: 'user:Run:Chat', name: 'Chat', program: 'chat.exe', scope: 'user', enabled: true },
        { id: 'machine:Run:Vendor', name: 'Vendor tray', program: 'tray.exe', scope: 'machine', enabled: true },
      ] }
      return snapshot()
    }),
    set_startup: vi.fn(async (id: string, enabled: boolean) => { tools().startup.items = tools().startup.items.map(item => item.id === id ? { ...item, enabled } : item); return snapshot() }),
    check_updates: vi.fn(async () => {
      tools().updates = { status: 'ready', current: null, error: null, packages: [
        { id: '7zip.7zip', name: '7-Zip', version: '26.04', available: '26.05', source: 'winget', status: 'pending' },
        { id: 'Git.Git', name: 'Git', version: '2.56.0', available: '2.56.1', source: 'winget', status: 'pending' },
      ] }
      return snapshot()
    }),
    install_updates: vi.fn(async (_ids: string[]) => snapshot()),
    stop_updates: vi.fn(async () => snapshot()),
    repair_windows: vi.fn(async () => snapshot()),
    get_files: vi.fn(async (_scanId: string, _categoryId: string, offset: number): Promise<FilesResponse> => ({ ok: true, items: files.slice(offset, offset + 2), total: files.length, next_offset: offset === 0 ? 2 : null })),
  } satisfies CleanerApi
  window.pywebview = { api }
  return { api, snapshot, update: (next: Partial<CleanerState>) => { state = { ...state, ...next } } }
}

async function connected() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze' })).toBeEnabled())
}

describe('appearance', () => {
  it('cycles the saved theme and applies it to the document', async () => {
    const { api } = bridge()
    render(<App />)
    await connected()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Theme: match Windows/ }))
    await waitFor(() => expect(api.set_theme).toHaveBeenCalledWith('light'))
    await waitFor(() => expect(document.documentElement).not.toHaveClass('dark'))
    await user.click(screen.getByRole('button', { name: /Theme: light/ }))
    await waitFor(() => expect(api.set_theme).toHaveBeenCalledWith('dark'))
    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
    expect(screen.getByRole('button', { name: /Theme: dark/ })).toBeEnabled()
  })
})

describe('maintenance tools', () => {
  async function openTools() {
    const mock = bridge()
    render(<App />)
    await connected()
    const user = userEvent.setup()
    await user.click(screen.getByRole('tab', { name: 'Tools' }))
    return { ...mock, user }
  }

  it('opens without changing anything except reading disks and startup apps', async () => {
    const { api } = await openTools()
    expect(screen.getByRole('tab', { name: 'Tools' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('button', { name: /Analyze/ })).not.toBeInTheDocument()
    await waitFor(() => expect(api.check_disks).toHaveBeenCalledOnce())
    expect(api.list_startup).toHaveBeenCalledOnce()
    expect(await screen.findByText('KINGSTON SA400', {}, { timeout: 2000 })).toBeInTheDocument()
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    for (const method of [api.flush_dns, api.test_connection, api.check_updates, api.install_updates, api.repair_windows, api.set_startup]) expect(method).not.toHaveBeenCalled()
  })

  it('updates only the programs left marked', async () => {
    const { api, user } = await openTools()
    await user.click(screen.getByRole('button', { name: 'Check for updates' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Git' }))
    await user.click(screen.getByRole('button', { name: 'Update selected (1)' }))
    expect(api.install_updates).toHaveBeenCalledWith(['7zip.7zip'])
  })

  it('runs DNS, connection and startup switches; repair needs administrator', async () => {
    const { api, user } = await openTools()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(await screen.findByText('DNS cache cleared')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Google' }))
    await user.click(screen.getByRole('button', { name: 'Test' }))
    expect(api.test_connection).toHaveBeenCalledWith('google')
    expect(await screen.findByText('Losing packets')).toBeInTheDocument()
    expect(screen.getByText('25%')).toBeInTheDocument()
    await user.click(await screen.findByRole('switch', { name: 'Chat' }))
    expect(api.set_startup).toHaveBeenCalledWith('user:Run:Chat', false)
    expect(screen.getByRole('switch', { name: 'Vendor tray' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Repair' })).not.toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Restart as administrator' })[0])
    expect(api.restart_as_administrator).toHaveBeenCalledOnce()
  })
})

describe('updates', () => {
  it('stays hidden until a release is available, then downloads and installs only on click', async () => {
    const { api, update } = bridge()
    render(<App />)
    await connected()
    expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument()
    act(() => update({ update: { status: 'available', version: '0.6.0', progress: null, error: null } }))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Update' }, { timeout: 2000 }))
    expect(api.download_update).toHaveBeenCalledOnce()
    expect(api.install_update).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: 'Restart to update' }))
    expect(api.install_update).toHaveBeenCalledOnce()
  })

  it('waits for a running cleanup before restarting, and can be dismissed', async () => {
    const { api, update } = bridge()
    render(<App />)
    await connected()
    act(() => update({ phase: 'cleaning', update: { status: 'ready', version: '0.6.0', progress: 1, error: null } }))
    expect(await screen.findByText('Finish the current work to install Clearspace 0.6.0.', {}, { timeout: 2000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Restart to update' })).toBeDisabled()
    act(() => update({ phase: 'idle', update: { status: 'available', version: '0.6.0', progress: null, error: null } }))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Not now' }, { timeout: 2000 }))
    expect(api.dismiss_update).toHaveBeenCalledOnce()
    await waitFor(() => expect(screen.queryByText('Clearspace 0.6.0 is available.')).not.toBeInTheDocument())
  })
})

describe('title bar', () => {
  it('drags from the header and forwards caption buttons, but not from controls', async () => {
    const { api } = bridge()
    const windowAction = vi.fn(async (_action: WindowAction) => ({ ok: true as const }))
    window.pywebview!.api.window_action = windowAction
    render(<App />)
    await connected()
    const user = userEvent.setup()
    const title = screen.getByRole('heading', { name: 'Clearspace' })
    fireEvent.mouseDown(title, { button: 0, screenX: 100, screenY: 20 })
    fireEvent.mouseMove(window, { screenX: 101, screenY: 21 })
    expect(windowAction).not.toHaveBeenCalled()
    fireEvent.mouseMove(window, { screenX: 110, screenY: 26 })
    expect(windowAction).toHaveBeenLastCalledWith('drag')
    fireEvent.mouseUp(window)
    fireEvent.mouseDown(title, { button: 0, screenX: 100, screenY: 20 })
    fireEvent.mouseUp(window)
    fireEvent.doubleClick(title)
    expect(windowAction.mock.calls.map(call => call[0])).toEqual(['drag', 'maximize'])
    windowAction.mockClear()
    await user.click(screen.getByRole('button', { name: /Theme:/ }))
    expect(windowAction).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Maximize' }))
    await user.click(screen.getByRole('button', { name: 'Minimize' }))
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(windowAction.mock.calls.map(call => call[0])).toEqual(['maximize', 'minimize', 'close'])
    expect(api.set_theme).toHaveBeenCalled()
  })
})

describe('cleanup flow', () => {
  it('starts with the selected profile and requires analysis before cleaning', async () => {
    const { api } = bridge()
    render(<App />)
    await connected()
    expect(screen.getByRole('checkbox', { name: /Temporary files/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Chrome cache/ })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Edge cache/ })).not.toBeVisible()
    expect(screen.queryByRole('button', { name: 'Clean selected' })).not.toBeInTheDocument()
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
    expect(screen.getByText(/removed · 2 items · 1 skipped/)).toBeVisible()
    expect(screen.getByText('Total size of deleted items. Disk free space may differ.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Clean selected' })).not.toBeInTheDocument()
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
    expect(screen.queryByRole('button', { name: 'Clean selected' })).not.toBeInTheDocument()
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
    expect(screen.queryByRole('button', { name: 'Clean selected' })).not.toBeInTheDocument()
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
    expect(screen.getByText(/removed · 1 item · 2 skipped · 1 could not be removed/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Clean selected' })).not.toBeInTheDocument()
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

describe('simple category selection', () => {
  it('shows available categories together without filter controls', async () => {
    bridge()
    render(<App />)
    await connected()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Select all' })).toBeEnabled()
    expect(screen.getByRole('checkbox', { name: 'Recycle Bin' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Chrome cache' })).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Python package cache' })).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Discord cache' })).toBeVisible()
    expect(screen.getByText('1 of 6 selected')).toBeVisible()
  })

  it('keeps unavailable categories out of the way and reveals their reason on request', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    expect(screen.getByRole('checkbox', { name: 'Edge cache' })).not.toBeVisible()
    await user.click(screen.getByText('Unavailable (2)'))
    expect(screen.getByRole('checkbox', { name: 'Edge cache' })).toBeDisabled()
    expect(screen.getByText('Close Microsoft Edge to analyze this cache.')).toBeVisible()
    expect(api.set_selection).not.toHaveBeenCalled()
  })

  it('selects every available category across all groups, including the Recycle Bin', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    expect(api.set_selection).toHaveBeenCalledExactlyOnceWith(['recycle_bin', 'user_temp', 'chrome_cache', 'pip_cache', 'windows_logs', 'discord_cache'])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Select all' })).toBeDisabled())
    expect(screen.getByText('6 of 6 selected')).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Recycle Bin' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Discord cache' })).toBeChecked()
    expect(api.scan).not.toHaveBeenCalled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('clears every selected category in one action', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(api.set_selection).toHaveBeenLastCalledWith([])
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze' })).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Clear selection' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Recycle Bin' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Temporary files' })).not.toBeChecked()
  })

  it('invalidates a prepared scan after selecting all', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Clean selected' })).not.toBeInTheDocument())
    expect(api.clean).not.toHaveBeenCalled()
  })
})

describe('Recycle Bin cleanup', () => {
  it('requires analysis and a specific native-bin confirmation before emptying', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    await api.set_selection(['recycle_bin'])
    render(<App />)
    await connected()
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    const row = screen.getByRole('checkbox', { name: 'Recycle Bin' }).closest('li')!
    expect(within(row).getByText('3 items')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'View files for Recycle Bin' })).not.toBeInTheDocument()
    expect(api.get_files).not.toHaveBeenCalled()
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove 2 KiB?' })
    expect(within(dialog).getByText('3 items from the selected categories will be permanently deleted.')).toBeVisible()
    expect(within(dialog).getByText('Windows empties the Recycle Bin when you confirm. Newly deleted items may be included. This cannot be undone or stopped once started.')).toBeVisible()
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Delete selected items' }))
    await waitFor(() => expect(api.clean).toHaveBeenCalledExactlyOnceWith('scan-1', ['recycle_bin']))
  })

  it('leaves the Recycle Bin untouched when confirmation is cancelled', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    await api.set_selection(['recycle_bin'])
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await user.click(await screen.findByRole('button', { name: 'Clean selected' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('includes every selected category and the native-bin scope after Select all', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await user.click(await screen.findByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('18 items from the selected categories will be permanently deleted.')).toBeVisible()
    expect(within(dialog).getByText('Recycle Bin · Temporary files · Chrome cache · Python package cache · Windows diagnostic logs · Discord cache')).toBeVisible()
    expect(api.clean).not.toHaveBeenCalled()
  })
})

describe('Windows cleanup actions', () => {
  it('supports an unknown-size action with zero files and confirms its native scope', async () => {
    const user = userEvent.setup()
    const { api } = bridge([nativeCategory('windows_components', 'Windows component cleanup', false)])
    await api.set_selection(['windows_components'])
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    const row = screen.getByRole('checkbox', { name: 'Windows component cleanup' }).closest('li')!
    expect(within(row).getByText('Managed by Windows')).toBeVisible()
    expect(within(row).getByText('Ready to clean')).toBeVisible()
    expect(screen.getByText('1 cleanup action')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'View files for Windows component cleanup' })).not.toBeInTheDocument()
    expect(screen.queryByText('Nothing to clean in the selected categories')).not.toBeInTheDocument()
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog', { name: 'Confirm cleanup' })
    expect(within(dialog).getByText('Run 1 cleanup action from the selected categories.')).toBeVisible()
    expect(within(dialog).getByText('Windows component cleanup')).toBeVisible()
    expect(within(dialog).getByText('Windows manages system cleanup and may not report an exact size. These operations cannot be stopped once Windows starts.')).toBeVisible()
    expect(api.clean).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Confirm cleanup' }))
    await waitFor(() => expect(api.clean).toHaveBeenCalledExactlyOnceWith('scan-1', ['windows_components']))
    expect(await screen.findByRole('heading', { name: 'Cleanup complete' })).toBeVisible()
    expect(screen.getByText('1 cleanup action completed')).toBeVisible()
    expect(screen.getByText('Windows did not report how much space these actions removed.')).toBeVisible()
    expect(screen.queryByText('0 B')).not.toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Cleanup action results' })).getByText('Completed')).toBeVisible()
  })

  it('clears saved history without implying the original files are removed', async () => {
    const user = userEvent.setup()
    const { api } = bridge([nativeCategory('recent_document_history', 'Recent documents')])
    await api.set_selection(['recent_document_history'])
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    const row = screen.getByRole('checkbox', { name: 'Recent documents' }).closest('li')!
    expect(within(row).getByText('History cleanup')).toBeVisible()
    expect(screen.getByText('History records only. Original files are kept.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog', { name: 'Confirm cleanup' })
    expect(within(dialog).getByText('History cleanup removes saved records. Your original files are kept.')).toBeVisible()
    expect(within(dialog).queryByText(/Windows manages system cleanup/)).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/permanently delete/)).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('includes file cleanup and native actions together without counting actions as files', async () => {
    const user = userEvent.setup()
    const { api } = bridge([nativeCategory('delivery_optimization', 'Delivery Optimization cache', false)])
    render(<App />)
    await connected()
    await user.click(screen.getByRole('checkbox', { name: 'Delivery Optimization cache' }))
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByText('2 KiB known')).toBeVisible()
    expect(screen.getByText('3 items · 1 cleanup action')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog', { name: 'Confirm cleanup' })
    expect(within(dialog).getByText('Run 1 cleanup action and permanently delete 3 items from the selected categories.')).toBeVisible()
    expect(within(dialog).getByText('Temporary files · Delivery Optimization cache')).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'Confirm cleanup' }))
    await waitFor(() => expect(api.clean).toHaveBeenCalledExactlyOnceWith('scan-1', ['user_temp', 'delivery_optimization']))
    expect(screen.getByText('Known size only. Windows did not report the size of every cleanup action.')).toBeVisible()
  })

  it('keeps cleanup disabled when an analyzed action reports nothing pending', async () => {
    const user = userEvent.setup()
    const { api, snapshot, update } = bridge([nativeCategory('run_history', 'Run dialog history')])
    await api.set_selection(['run_history'])
    api.scan.mockImplementationOnce(async () => { update({ phase: 'ready', scan_id: 'empty-action' }); return snapshot() })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByText('Nothing to clean in the selected categories')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
    // Analyzed categories with nothing to clean are grouped out of the way.
    await user.click(screen.getByText('Nothing to clean (1)'))
    expect(within(screen.getByRole('checkbox', { name: 'Run dialog history' }).closest('li')!).getByText('Nothing to clean')).toBeVisible()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('moves analyzed categories with nothing to clean into a collapsed group', async () => {
    const user = userEvent.setup()
    const { api, snapshot, update } = bridge()
    await api.set_selection(['user_temp', 'recycle_bin'])
    api.scan.mockImplementationOnce(async () => {
      const current = snapshot()
      if (!current.ok) throw new Error('unexpected bridge state')
      update({ phase: 'ready', scan_id: 'partly-empty', categories: current.state.categories.map(category =>
        category.id === 'user_temp' ? { ...category, files: 3, bytes: 2048 } : { ...category, files: 0, bytes: 0 }) })
      return snapshot()
    })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    const summary = await screen.findByText('Nothing to clean (1)')
    expect(screen.getByRole('checkbox', { name: 'Recycle Bin' })).not.toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Temporary files' })).toBeVisible()
    await user.click(summary)
    expect(screen.getByRole('checkbox', { name: 'Recycle Bin' })).toBeVisible()
  })

  it('reports a failed native analysis as needing attention instead of an empty successful scan', async () => {
    const user = userEvent.setup()
    const failed = { ...nativeCategory('windows_components', 'Windows component cleanup', false), selected: true,
      analysis_error: true, reason: 'Administrator permission required.' }
    const { api, snapshot, update } = bridge([failed])
    await api.set_selection(['windows_components'])
    api.scan.mockImplementationOnce(async () => {
      update({ phase: 'ready', scan_id: 'failed-native', categories: [failed] })
      return snapshot()
    })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByText('Some categories could not be analyzed')).toBeVisible()
    expect(screen.getByText('Review the category messages and analyze again.')).toBeVisible()
    expect(screen.getByText('Administrator permission required.')).toBeVisible()
    expect(screen.getByText('Needs attention')).toBeVisible()
    expect(document.querySelector('.app-footer')).toHaveTextContent('Analysis needs attention')
    expect(document.querySelector('.empty-result-warning .lucide-circle-alert')).toBeInTheDocument()
    expect(document.querySelector('.empty-result .lucide-check')).not.toBeInTheDocument()
    expect(screen.queryByText(/Nothing to clean/)).not.toBeInTheDocument()
    expect(screen.queryByText('0 B')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('keeps valid file cleanup available when another selected category could not be analyzed', async () => {
    const user = userEvent.setup()
    const failed = { ...nativeCategory('windows_components', 'Windows component cleanup', false), selected: true,
      analysis_error: true, reason: 'Administrator permission required.' }
    const { api, snapshot, update } = bridge([failed])
    api.scan.mockImplementationOnce(async () => {
      const temporary = { ...initial().categories.find(category => category.id === 'user_temp')!, files: 3, bytes: 2048 }
      update({ phase: 'ready', scan_id: 'mixed-scan', categories: [temporary, failed] })
      return snapshot()
    })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    expect(screen.getByText('Needs attention')).toBeVisible()
    expect(screen.queryByText('Nothing to clean in the selected categories')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove 2 KiB?' })
    expect(within(dialog).getByText('Temporary files')).toBeVisible()
    expect(within(dialog).queryByText('Windows component cleanup')).not.toBeInTheDocument()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('shows deferred Windows work as pending without claiming failure or zero-byte success', async () => {
    const { update } = bridge()
    update({ phase: 'completed', result: { removed: 0, freed_bytes: 0, skipped: 0, failed: 0, cancelled: false,
      actions_completed: 0, actions_failed: 0, actions_deferred: 1, actions: [
        { category_id: 'delivery_optimization', title: 'Delivery Optimization cache', status: 'deferred', freed_bytes: null,
          detail: 'Windows maintenance is still running. Analyze again after it finishes.' },
      ] } })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Cleanup finished with pending work' })).toBeVisible()
    expect(document.querySelector('.app-footer')).toHaveTextContent('Cleanup finished with pending work')
    expect(screen.getByText('1 cleanup action pending')).toBeVisible()
    const list = screen.getByRole('list', { name: 'Cleanup action results' })
    expect(within(list).getByText('Pending')).toBeVisible()
    expect(within(list).getByText('Windows maintenance is still running. Analyze again after it finishes.')).toBeVisible()
    expect(within(list).queryByText('Failed')).not.toBeInTheDocument()
    expect(document.querySelector('.result-icon-warning .lucide-clock3')).toBeInTheDocument()
    expect(document.querySelector('.cleanup-action-list .lucide-clock3')).toHaveClass('text-warning')
    expect(screen.queryByText('0 B')).not.toBeInTheDocument()
    expect(screen.queryByText(/0 cleanup actions completed/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Cleanup complete' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Cleanup finished with errors' })).not.toBeInTheDocument()
  })

  it('keeps removed file totals separate from pending maintenance', async () => {
    const { update } = bridge()
    update({ phase: 'completed', result: { removed: 4, freed_bytes: 4096, skipped: 0, failed: 0, cancelled: false,
      actions_completed: 1, actions_failed: 0, actions_deferred: 1, actions: [
        { category_id: 'run_history', title: 'Run dialog history', status: 'completed', freed_bytes: 0, detail: null },
        { category_id: 'delivery_optimization', title: 'Delivery Optimization cache', status: 'deferred', freed_bytes: null, detail: null },
      ] } })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Cleanup finished with pending work' })).toBeVisible()
    expect(screen.getByText(/removed · 4 items · 1 cleanup action completed · 1 cleanup action pending/)).toBeVisible()
    expect(screen.getByText('Known size only. Pending Windows cleanup is not included.')).toBeVisible()
    expect(screen.getByText('Windows maintenance is still running. Analyze again after it finishes.')).toBeVisible()
    expect(screen.queryByText(/cleanup action failed/)).not.toBeInTheDocument()
  })

  it('shows a maintenance wait message and keeps Stop available before the next operation starts', async () => {
    const user = userEvent.setup()
    const { api, update } = bridge()
    update({ phase: 'cleaning', progress: { checked: 2, files: 2, bytes: 4096, current: 'delivery_optimization',
      message: 'Waiting for Windows maintenance to finish' } })
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled())
    expect(screen.getAllByText('Waiting for Windows maintenance to finish').every(element => element.textContent === 'Waiting for Windows maintenance to finish')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(api.cancel).toHaveBeenCalledOnce())
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('keeps a still-running Windows analysis distinct from failure and an empty successful scan', async () => {
    const user = userEvent.setup()
    const pendingCategory = { ...nativeCategory('windows_components', 'Windows component cleanup', false), selected: true,
      analysis_pending: true, analysis_error: false, reason: 'Windows is still analyzing update cleanup.' }
    const { api, snapshot, update } = bridge([pendingCategory])
    await api.set_selection(['windows_components'])
    api.scan.mockImplementationOnce(async () => {
      update({ phase: 'ready', scan_id: 'pending-native', categories: [pendingCategory] })
      return snapshot()
    })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    expect(await screen.findByText('Windows analysis is still running')).toBeVisible()
    expect(screen.getAllByText('Analyze again after Windows finishes.').every(element => element.textContent === 'Analyze again after Windows finishes.')).toBe(true)
    expect(document.querySelector('.app-footer')).toHaveTextContent('Waiting for Windows')
    const row = screen.getByRole('checkbox', { name: 'Windows component cleanup' }).closest('li')!
    expect(within(row).getByText('Waiting for Windows')).toBeVisible()
    expect(document.querySelector('.empty-result-warning .lucide-clock3')).toBeInTheDocument()
    expect(screen.queryByText(/Nothing to clean/)).not.toBeInTheDocument()
    expect(screen.queryByText('Some categories could not be analyzed')).not.toBeInTheDocument()
    expect(screen.queryByText('0 B')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clean selected' })).toBeDisabled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('keeps analyzed files usable while a selected Windows analysis is still running', async () => {
    const user = userEvent.setup()
    const pendingCategory = { ...nativeCategory('windows_components', 'Windows component cleanup', false), selected: true,
      analysis_pending: true, analysis_error: false, reason: 'Windows is still analyzing update cleanup.' }
    const { api, snapshot, update } = bridge([pendingCategory])
    api.scan.mockImplementationOnce(async () => {
      const temporary = { ...initial().categories.find(category => category.id === 'user_temp')!, files: 3, bytes: 2048 }
      update({ phase: 'ready', scan_id: 'mixed-pending', categories: [temporary, pendingCategory] })
      return snapshot()
    })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Analyze' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Clean selected' })).toBeEnabled())
    expect(screen.getByText('Waiting for Windows')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Clean selected' }))
    expect(within(screen.getByRole('dialog', { name: 'Remove 2 KiB?' })).getByText('Temporary files')).toBeVisible()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it.each([
    ['analyze', 'Windows is still analyzing cleanup options.', 'Windows maintenance is still running.'],
    ['cleanup', 'Windows maintenance is still running.', 'Windows is still analyzing cleanup options.'],
  ] as const)('describes an ongoing background %s operation without confusing analysis and cleanup', async (operation, message, otherMessage) => {
    const { api, update } = bridge()
    update({ phase: 'cancelled', maintenance: { action_id: 'windows_components', operation, phase: 'finishing', elapsed_seconds: 12 } })
    render(<App />)
    await connected()
    expect(screen.getByText(message)).toBeVisible()
    expect(screen.queryByText(otherMessage)).not.toBeInTheDocument()
    expect(api.scan).not.toHaveBeenCalled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('shows native failures and skipped actions alongside successful cleanup', async () => {
    const { update } = bridge()
    update({ phase: 'completed', result: { removed: 1, freed_bytes: 1024, skipped: 0, failed: 0, cancelled: false,
      actions_completed: 1, actions_failed: 1, actions: [
        { category_id: 'run_history', title: 'Run dialog history', status: 'completed', freed_bytes: 0, detail: 'Saved records cleared.' },
        { category_id: 'windows_components', title: 'Windows component cleanup', status: 'failed', freed_bytes: null, detail: 'Administrator permission required.' },
        { category_id: 'delivery_optimization', title: 'Delivery Optimization cache', status: 'skipped', freed_bytes: null, detail: 'Cleanup stopped before this action started.' },
      ] } })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Cleanup finished with errors' })).toBeVisible()
    expect(document.querySelector('.app-footer')).toHaveTextContent('Cleanup finished with errors')
    const list = screen.getByRole('list', { name: 'Cleanup action results' })
    expect(within(list).getByText('Completed')).toBeVisible()
    expect(within(list).getByText('Failed')).toBeVisible()
    expect(within(list).getByText('Skipped')).toBeVisible()
    expect(within(list).getByText('Administrator permission required.')).toBeVisible()
    expect(screen.getByText(/1 cleanup action failed/)).toBeVisible()
  })
})

describe('Windows Search settings shortcut', () => {
  it('explains recent apps and opens Windows Settings only after a click', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    render(<App />)
    await connected()
    expect(screen.getByText('Windows Search recent apps')).toBeVisible()
    expect(screen.getByText('Clear these in Windows Settings. Recent document history is a separate list.')).toBeVisible()
    expect(api.open_search_settings).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Open search history settings' }))
    await waitFor(() => expect(api.open_search_settings).toHaveBeenCalledExactlyOnceWith())
    expect(api.scan).not.toHaveBeenCalled()
    expect(api.clean).not.toHaveBeenCalled()
  })

  it('shows a readable error when Windows Settings cannot be opened', async () => {
    const user = userEvent.setup()
    const { api } = bridge()
    api.open_search_settings.mockResolvedValueOnce({ ok: false, error: 'Windows Search settings could not be opened.' })
    render(<App />)
    await connected()
    await user.click(screen.getByRole('button', { name: 'Open search history settings' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Windows Search settings could not be opened.')
    expect(api.clean).not.toHaveBeenCalled()
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

describe('language, permission and disk space', () => {
  it('switches the interface, including service text, to Portuguese', async () => {
    const { api } = bridge()
    render(<App />)
    await connected()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Change language' }))
    await waitFor(() => expect(api.set_language).toHaveBeenCalledWith('pt'))
    expect(await screen.findByRole('button', { name: 'Analisar' })).toBeVisible()
    expect(screen.getByText('Limpe seu PC')).toBeVisible()
    expect(screen.getByRole('checkbox', { name: 'Arquivos temporários' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Mudar idioma' })).toHaveTextContent('PT')
    await user.click(screen.getByRole('button', { name: 'Mudar idioma' }))
    await waitFor(() => expect(api.set_language).toHaveBeenLastCalledWith('en'))
    expect(await screen.findByRole('button', { name: 'Analyze' })).toBeVisible()
  })

  it('offers an administrator restart only when selected system cleanup needs it', async () => {
    const { api, update } = bridge()
    update({ administrator: false })
    render(<App />)
    await connected()
    expect(screen.queryByRole('button', { name: 'Restart as administrator' })).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('checkbox', { name: 'Windows diagnostic logs' }))
    await user.click(await screen.findByRole('button', { name: 'Restart as administrator' }))
    await waitFor(() => expect(api.restart_as_administrator).toHaveBeenCalledOnce())
    update({ administrator: true })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Restart as administrator' })).not.toBeInTheDocument())
  })

  it('shows the free space of the system drive', async () => {
    const { update } = bridge()
    update({ disk: { drive: 'C:', free: 512 * 1024 ** 3, total: 1024 * 1024 ** 3 } })
    render(<App />)
    await connected()
    expect(screen.getByText('Disk C:')).toBeVisible()
    expect(screen.getByText('512 GiB free of 1 TiB')).toBeVisible()
    expect(screen.getByRole('meter', { name: 'Used space' })).toHaveAttribute('aria-valuenow', String(512 * 1024 ** 3))
  })
})

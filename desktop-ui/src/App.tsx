import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, ChevronRight, CircleAlert, Clock3, ExternalLink, HardDrive, LoaderCircle, Search, ShieldCheck, Trash2, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { CategoryRow } from './components/CategoryRow'
import { ThemeToggle, useThemeClass } from './components/ThemeToggle'
import { LanguageToggle } from './components/LanguageToggle'
import { WindowControls, dragFromHeader, maximizeFromHeader } from './components/TitleBar'
import { CategoryToolbar } from './components/CategoryToolbar'
import { CleanupResult } from './components/CleanupResult'
import { FilesDialog, type FileSelection } from './components/FilesDialog'
import { countActions, countFiles, countItems, formatBytes, isHistoryAction, isNativeAction, isRecycleBin } from './format'
import { useCleaner } from './use-cleaner'
import { locale, resolveLanguage, setLanguage, t } from './i18n'
import type { Category } from './types'

interface Review {
  scanId: string; ids: string[]; bytes: number; files: number; titles: string[];
  actions: number; recycleBin: boolean; history: boolean; maintenance: boolean
}

export default function App() {
  const { state, connected, pending, error, invoke, dismissError } = useCleaner()
  const theme = state.theme ?? 'system'
  useThemeClass(theme)
  setLanguage(resolveLanguage(state.language))
  const [details, setDetails] = useState<FileSelection | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const content = useRef<HTMLElement>(null)
  const selected = state.categories.filter(category => category.selected && category.available)
  const selectedBytes = selected.reduce((sum, category) => sum + category.bytes, 0)
  const selectedFiles = selected.reduce((sum, category) => sum + category.files, 0)
  const selectedActions = selected.reduce((sum, category) => sum + (category.actions || 0), 0)
  const analysisError = selected.some(category => category.analysis_error)
  const analysisPending = selected.some(category => category.analysis_pending)
  const historyOnly = selected.filter(category => category.files > 0 || (category.actions || 0) > 0).every(isHistoryAction)
  const available = state.categories.filter(category => category.available)
  const unavailable = state.categories.filter(category => !category.available)
  const analyzedNow = state.phase === 'ready' && state.scan_id !== null
  // After an analysis, selected categories with nothing to clean move out of the way.
  const isEmpty = (category: Category) => analyzedNow && category.selected && category.files === 0 && !(category.actions ?? 0)
    && !category.analysis_error && !category.analysis_pending
  const listed = available.filter(category => !isEmpty(category))
  const empty = available.filter(isEmpty)
  const groups = [...new Set(listed.map(category => category.group || 'Windows'))]
  const systemCategory = (category: Category) => category.group === 'System maintenance' || category.id.startsWith('windows_')
    || ['crash_dumps', 'system_memory_dump', 'error_reports'].includes(category.id) || /administrator/i.test(category.reason ?? '')
  const needsAdministrator = state.administrator === false && selected.some(systemCategory)
  const disk = state.disk
  const busy = state.phase === 'scanning' || state.phase === 'cleaning'
  const disabled = !connected || busy || pending !== null
  const analyzed = state.scan_id !== null
  const ready = state.phase === 'ready' && analyzed
  const canClean = !disabled && ready && (selectedFiles > 0 || selectedActions > 0)
  const reviewOpen = review !== null && ready && review.scanId === state.scan_id
  const result = state.result
  const resultFailed = Boolean(result && (result.failed > 0 || (result.actions_failed ?? result.actions?.filter(action => action.status === 'failed').length ?? 0) > 0))
  const resultPending = (result?.actions_deferred ?? result?.actions?.filter(action => action.status === 'deferred').length ?? 0) > 0
  useEffect(() => {
    if (state.phase === 'completed' || state.phase === 'cancelled') content.current?.scrollTo({ top: 0 })
  }, [state.phase])
  const currentCategory = state.categories.find(category => category.id === state.progress.current)?.title
  const status = state.phase === 'cleaning' && state.progress.message ? t(state.progress.message) : t(state.phase === 'scanning' ? 'Analyzing selected categories' : state.phase === 'cleaning' ? 'Cleaning selected items'
    : state.phase === 'ready' ? selectedFiles > 0 || selectedActions > 0 ? 'Analysis complete' : analysisError ? 'Analysis needs attention' : analysisPending ? 'Waiting for Windows' : 'Nothing to clean'
    : state.phase === 'completed' ? resultFailed ? 'Cleanup finished with errors' : resultPending ? 'Cleanup finished with pending work' : 'Cleanup complete'
    : state.phase === 'cancelled' ? 'Stopped' : state.phase === 'error' ? 'Something needs attention' : 'Ready when you are')
  const readySummary = selectedFiles === 0 && selectedActions === 0 && analysisError ? t('Analysis needs attention')
    : selectedFiles === 0 && selectedActions === 0 && analysisPending ? t('Waiting for Windows')
    : selectedFiles === 0 && selectedActions > 0 ? countActions(selectedActions) : t(selectedActions > 0 ? '{size} known' : '{size} selected', { size: formatBytes(selectedBytes) })
  const readyDetails = selectedFiles === 0 && selectedActions === 0 && analysisError ? t('Review the category messages.')
    : selectedFiles === 0 && selectedActions === 0 && analysisPending ? t('Analyze again after Windows finishes.')
    : selectedFiles > 0 || selectedActions === 0 ? `${countItems(selectedFiles)}${selectedActions > 0 ? ` · ${countActions(selectedActions)}` : ''}`
    : t(historyOnly ? 'History records only. Original files are kept.' : 'Windows manages these cleanup actions.')
  const reviewDescription = review?.actions ? review.files > 0 ? t('Run {actions} and permanently delete {items} from the selected categories.', { actions: countActions(review.actions), items: countItems(review.files) })
    : t('Run {actions} from the selected categories.', { actions: countActions(review.actions) })
    : t('{files} from the selected categories will be permanently deleted.', { files: (review?.recycleBin ? countItems : countFiles)(review?.files ?? 0) })
  const checked = t('{count} checked', { count: state.progress.checked.toLocaleString(locale()) })

  function select(id: string, checked: boolean) {
    updateSelection(state.categories.filter(category => category.available && (category.id === id ? checked : category.selected)).map(category => category.id))
  }
  function updateSelection(ids: string[]) {
    if (ids.length === selected.length && selected.every(category => ids.includes(category.id))) return
    setReview(null)
    setDetails(null)
    void invoke('set_selection', ids)
  }
  function analyze() {
    setDetails(null)
    setReview(null)
    content.current?.scrollTo({ top: 0 })
    void invoke('scan')
  }
  function requestClean() {
    if (!canClean || !state.scan_id) return
    const prepared = selected.filter(category => category.files > 0 || (category.actions || 0) > 0)
    setReview({ scanId: state.scan_id, ids: selected.map(category => category.id), bytes: selectedBytes, files: selectedFiles,
      titles: prepared.map(category => category.title), actions: selectedActions, recycleBin: prepared.some(isRecycleBin),
      history: prepared.some(isHistoryAction), maintenance: prepared.some(category => isNativeAction(category) && !isHistoryAction(category)) })
  }
  async function clean() {
    if (!review || !canClean || review.scanId !== state.scan_id) return
    await invoke('clean', review.scanId, review.ids)
    setReview(null)
  }

  return <div className="app-shell">
    <header className={connected ? 'app-header with-window-controls' : 'app-header'} onMouseDown={dragFromHeader} onDoubleClick={maximizeFromHeader}>{connected ? <WindowControls /> : null}<div className="app-brand"><HardDrive className="size-5 text-muted-foreground" aria-hidden="true" /><h1>Clearspace</h1></div><div className="app-header-actions"><LanguageToggle language={state.language ?? 'system'} disabled={!connected} onChange={value => void invoke('set_language', value)} /><ThemeToggle theme={theme} disabled={!connected} onChange={value => void invoke('set_theme', value)} /></div></header>
    {!connected ? <div className="notice" role="status"><CircleAlert className="size-4 shrink-0" /><span>{t('Waiting for the desktop connection. Open Clearspace with start.cmd.')}</span></div> : null}
    {error || state.error ? <div className="notice notice-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{error || t(state.error ?? '')}</span>{error ? <Button variant="ghost" size="icon" aria-label={t('Dismiss error')} onClick={dismissError}><X /></Button> : null}</div> : null}
    {state.maintenance && !busy ? <div className="notice" role="status"><Clock3 className="size-4 shrink-0 text-warning" aria-hidden="true" /><span>{t(state.maintenance.operation === 'analyze' ? 'Windows is still analyzing cleanup options.' : 'Windows maintenance is still running.')}</span></div> : null}
    {needsAdministrator ? <div className="notice admin-notice" role="status"><ShieldCheck className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><span>{t('System cleanup needs administrator permission. Protected files are skipped until then.')}</span>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => void invoke('restart_as_administrator')}>{pending === 'restart_as_administrator' ? <LoaderCircle className="animate-spin" /> : <ShieldCheck />}{t('Restart as administrator')}</Button></div> : null}
    <main className="app-content" ref={content}>
      <section aria-labelledby="cleanup-heading">
        <div className="section-heading"><div><h2 id="cleanup-heading">{t('Clean up your PC')}</h2><p>{t('Select what to clean, then analyze.')}</p></div>
          {busy ? <Button variant="outline" disabled><LoaderCircle className="animate-spin" />{t(state.phase === 'scanning' ? 'Analyzing…' : 'Cleaning…')}</Button>
            : analyzed || result ? <Button variant="outline" disabled={disabled || selected.length === 0} onClick={analyze}><Search />{t('Analyze again')}</Button> : null}
        </div>
        {disk && disk.total > 0 ? <div className="disk-meter" aria-label={t('Disk space')}>
          <div><span>{t('Disk {drive}', { drive: disk.drive })}</span><span>{t('{free} free of {total}', { free: formatBytes(disk.free), total: formatBytes(disk.total) })}</span></div>
          <div className="disk-meter-track" role="meter" aria-valuemin={0} aria-valuemax={disk.total} aria-valuenow={disk.total - disk.free} aria-label={t('Used space')}><span style={{ width: `${Math.min(100, Math.max(0, (1 - disk.free / disk.total) * 100))}%` }} /></div>
        </div> : null}
        {result ? <CleanupResult result={result} /> : null}
        {busy ? <div className="operation-progress" role="status" aria-live="polite"><div><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /><span>{state.progress.message ? t(state.progress.message) : `${t(state.phase === 'scanning' ? 'Analyzing' : 'Cleaning')}${currentCategory ? ` · ${t(currentCategory)}` : '…'}`}</span><span>{checked}</span></div><div className="indeterminate-track" aria-hidden="true"><span /></div></div> : null}
        {ready && selectedFiles === 0 && selectedActions === 0 ? <div className={`empty-result${analysisError || analysisPending ? ' empty-result-warning' : ''}`} role="status">
          {analysisError ? <CircleAlert className="size-5" aria-hidden="true" /> : analysisPending ? <Clock3 className="size-5" aria-hidden="true" /> : <Check className="size-5" aria-hidden="true" />}
          <div><p>{t(analysisError ? 'Some categories could not be analyzed' : analysisPending ? 'Windows analysis is still running' : 'Nothing to clean in the selected categories')}</p><span>{t(analysisError ? 'Review the category messages and analyze again.' : analysisPending ? 'Analyze again after Windows finishes.' : 'You can select other categories or analyze again later.')}</span></div>
        </div> : null}
        {state.phase === 'cancelled' && !result ? <p className="field-help analysis-message" role="status">{t('Analysis stopped. Run Analyze again to prepare a complete file list.')}</p> : null}
        <CategoryToolbar disabled={disabled} selected={selected.length} available={available.length}
          onSelectAll={() => updateSelection(available.map(category => category.id))} onClear={() => updateSelection([])} />
        <div className="category-groups" aria-busy={busy}>
          {groups.map(group => <section key={group} aria-label={t('{group} cleanup', { group: t(group) })}>
            <h3 className="category-group-heading">{t(group)}</h3>
            <ul className="category-list" aria-label={t('{group} categories', { group: t(group) })}>
              {listed.filter(category => (category.group || 'Windows') === group).map(category => <CategoryRow key={category.id} category={category} disabled={disabled} analyzed={analyzed}
                detailsEnabled={connected && analyzed && !busy && pending === null}
                onSelect={checked => select(category.id, checked)} onDetails={() => { if (state.scan_id) setDetails({ scanId: state.scan_id, categoryId: category.id, title: category.title }) }} />)}
            </ul>
          </section>)}
        </div>
        {available.length === 0 ? <div className="category-empty" role="status"><p>{t(!connected ? 'Loading cleanup categories…' : 'No cleanup categories available')}</p></div> : null}
        {empty.length > 0 ? <details className="unavailable-categories">
          <summary><ChevronRight className="summary-chevron" aria-hidden="true" />{t('Nothing to clean ({count})', { count: empty.length })}</summary>
          <ul className="category-list" aria-label={t('Categories with nothing to clean')}>
            {empty.map(category => <CategoryRow key={category.id} category={category} disabled={disabled} analyzed={analyzed} detailsEnabled={false}
              onSelect={checked => select(category.id, checked)} onDetails={() => {}} />)}
          </ul>
        </details> : null}
        {unavailable.length > 0 ? <details className="unavailable-categories">
          <summary><ChevronRight className="summary-chevron" aria-hidden="true" />{t('Unavailable ({count})', { count: unavailable.length })}</summary>
          <ul className="category-list" aria-label={t('Unavailable cleanup categories')}>
            {unavailable.map(category => <CategoryRow key={category.id} category={category} disabled analyzed={false} detailsEnabled={false} onSelect={() => {}} onDetails={() => {}} />)}
          </ul>
        </details> : null}
        <section className="search-history-settings" aria-labelledby="search-history-heading">
          <div><h3 id="search-history-heading">{t('Windows Search recent apps')}</h3><p>{t('Clear these in Windows Settings. Recent document history is a separate list.')}</p></div>
          <Button variant="outline" disabled={disabled} onClick={() => void invoke('open_search_settings')}><ExternalLink />{t('Open search history settings')}</Button>
        </section>
      </section>
    </main>
    <div className="action-bar"><div><p>{busy ? status : ready ? readySummary : t(selected.length === 1 ? '{count} category selected' : '{count} categories selected', { count: selected.length })}</p><span>{busy ? t('{count} items checked', { count: state.progress.checked.toLocaleString(locale()) }) : ready ? readyDetails : t('Analyze to see what can be removed.')}</span></div>{busy ? <Button variant="outline" disabled={!connected || pending !== null} onClick={() => void invoke('cancel')}>{pending === 'cancel' ? <LoaderCircle className="animate-spin" /> : <X />}{t(pending === 'cancel' ? 'Stopping…' : 'Stop')}</Button> : ready ? <Button disabled={!canClean} onClick={requestClean}><Trash2 />{t('Clean selected')}<ArrowRight /></Button> : <Button disabled={disabled || selected.length === 0} onClick={analyze}>{pending === 'scan' ? <LoaderCircle className="animate-spin" /> : <Search />}{t('Analyze')}<ArrowRight /></Button>}</div>
    <footer className="app-footer"><span>{status}</span><span>{state.version ? `v${state.version}` : 'Clearspace'}</span></footer>
    <FilesDialog selection={details} scanId={state.scan_id} onClose={() => setDetails(null)} />
    <Dialog open={reviewOpen} onOpenChange={value => { if (!value) setReview(null) }}><DialogContent className="confirm-dialog">
      <DialogHeader><DialogTitle>{review?.actions ? t('Confirm cleanup') : t('Remove {size}?', { size: formatBytes(review?.bytes ?? 0) })}</DialogTitle><DialogDescription>{reviewDescription}</DialogDescription></DialogHeader>
      <p className="review-categories">{review?.titles.map(title => t(title)).join(' · ')}</p>
      {review?.recycleBin || review?.maintenance || review?.history ? <div className="review-cleanup-notes">
        {review.recycleBin ? <p>{t('Windows empties the Recycle Bin when you confirm. Newly deleted items may be included. This cannot be undone or stopped once started.')}</p> : null}
        {review.maintenance ? <p>{t('Windows manages system cleanup and may not report an exact size. These operations cannot be stopped once Windows starts.')}</p> : null}
        {review.history ? <p>{t('History cleanup removes saved records. Your original files are kept.')}</p> : null}
      </div> : null}
      <DialogFooter><Button variant="outline" disabled={pending !== null} onClick={() => setReview(null)}>{t('Cancel')}</Button><Button disabled={!canClean || pending !== null} onClick={() => void clean()}>{pending === 'clean' ? <LoaderCircle className="animate-spin" /> : <Trash2 />}{t(review?.actions ? 'Confirm cleanup' : review?.recycleBin ? 'Delete selected items' : 'Delete selected files')}</Button></DialogFooter>
    </DialogContent></Dialog>
  </div>
}

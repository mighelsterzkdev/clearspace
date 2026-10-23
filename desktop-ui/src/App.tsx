import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, CircleAlert, HardDrive, LoaderCircle, Search, Trash2, X } from 'lucide-react'
import { Button } from './components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './components/ui/dialog'
import { CategoryRow } from './components/CategoryRow'
import { CategoryToolbar } from './components/CategoryToolbar'
import { CleanupResult } from './components/CleanupResult'
import { FilesDialog, type FileSelection } from './components/FilesDialog'
import { countFiles, formatBytes } from './format'
import { useCleaner } from './use-cleaner'

interface Review { scanId: string; ids: string[]; bytes: number; files: number; titles: string[] }

export default function App() {
  const { state, connected, pending, error, invoke, dismissError } = useCleaner()
  const [details, setDetails] = useState<FileSelection | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [group, setGroup] = useState('all')
  const [query, setQuery] = useState('')
  const [showUnavailable, setShowUnavailable] = useState(false)
  const content = useRef<HTMLElement>(null)
  const selected = state.categories.filter(category => category.selected && category.available)
  const selectedBytes = selected.reduce((sum, category) => sum + category.bytes, 0)
  const selectedFiles = selected.reduce((sum, category) => sum + category.files, 0)
  const recommended = state.categories.filter(category => category.available && category.recommended).map(category => category.id)
  const recommendedSelected = recommended.length === selected.length && selected.every(category => recommended.includes(category.id))
  const groups = [...new Set(state.categories.map(category => category.group || 'Windows'))]
  const search = query.trim().toLocaleLowerCase('en-US')
  const visible = state.categories.filter(category => (showUnavailable || category.available)
    && (group === 'all' || (category.group || 'Windows') === group)
    && (!search || `${category.title} ${category.description} ${category.reason || ''} ${category.group || 'Windows'}`.toLocaleLowerCase('en-US').includes(search)))
  const hiddenSelected = selected.filter(category => !visible.some(item => item.id === category.id)).length
  const unavailable = state.categories.filter(category => !category.available).length
  const busy = state.phase === 'scanning' || state.phase === 'cleaning'
  const disabled = !connected || busy || pending !== null
  const analyzed = state.scan_id !== null
  const ready = state.phase === 'ready' && analyzed
  const canClean = !disabled && ready && selectedFiles > 0
  const reviewOpen = review !== null && ready && review.scanId === state.scan_id
  const result = state.result
  useEffect(() => {
    if (state.phase === 'completed' || state.phase === 'cancelled') content.current?.scrollTo({ top: 0 })
  }, [state.phase])
  const currentCategory = state.categories.find(category => category.id === state.progress.current)?.title
  const status = state.phase === 'scanning' ? 'Analyzing selected categories' : state.phase === 'cleaning' ? 'Cleaning selected files'
    : state.phase === 'ready' ? selectedFiles > 0 ? 'Analysis complete' : 'Nothing to clean'
    : state.phase === 'completed' ? 'Cleanup complete' : state.phase === 'cancelled' ? 'Stopped' : state.phase === 'error' ? 'Something needs attention' : 'Ready when you are'

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
    if (canClean && state.scan_id) setReview({ scanId: state.scan_id, ids: selected.map(category => category.id), bytes: selectedBytes, files: selectedFiles, titles: selected.filter(category => category.files > 0).map(category => category.title) })
  }
  async function clean() {
    if (!review || !canClean || review.scanId !== state.scan_id) return
    await invoke('clean', review.scanId, review.ids)
    setReview(null)
  }

  return <div className="app-shell">
    <header className="app-header"><div className="app-brand"><HardDrive className="size-5 text-muted-foreground" aria-hidden="true" /><h1>Clearspace</h1></div><span className="app-caption">Windows cleaner</span></header>
    {!connected ? <div className="notice" role="status"><CircleAlert className="size-4 shrink-0" /><span>Waiting for the desktop connection. Open Clearspace with start.cmd.</span></div> : null}
    {error || state.error ? <div className="notice notice-error" role="alert"><CircleAlert className="size-4 shrink-0" /><span>{error || state.error}</span>{error ? <Button variant="ghost" size="icon" aria-label="Dismiss error" onClick={dismissError}><X /></Button> : null}</div> : null}
    <main className="app-content" ref={content}>
      <section aria-labelledby="cleanup-heading">
        <div className="section-heading"><div><h2 id="cleanup-heading">Clean up your PC</h2><p>Analyze selected categories, then review files before cleaning.</p></div>
          {busy ? <Button variant="outline" disabled><LoaderCircle className="animate-spin" />{state.phase === 'scanning' ? 'Analyzing…' : 'Cleaning…'}</Button>
            : analyzed || result ? <Button variant="outline" disabled={disabled || selected.length === 0} onClick={analyze}><Search />Analyze again</Button> : null}
        </div>
        {result ? <CleanupResult result={result} /> : null}
        {busy ? <div className="operation-progress" role="status" aria-live="polite"><div><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /><span>{state.phase === 'scanning' ? 'Analyzing' : 'Cleaning'}{currentCategory ? ` · ${currentCategory}` : '…'}</span><span>{state.progress.checked.toLocaleString('en-US')} checked</span></div><div className="indeterminate-track" aria-hidden="true"><span /></div></div> : null}
        {ready && selectedFiles === 0 ? <div className="empty-result" role="status"><Check className="size-5" aria-hidden="true" /><div><p>Nothing to clean in the selected categories</p><span>You can select other categories or analyze again later.</span></div></div> : null}
        {state.phase === 'cancelled' && !result ? <p className="field-help analysis-message" role="status">Analysis stopped. Run Analyze again to prepare a complete file list.</p> : null}
        <CategoryToolbar groups={groups} group={group} query={query} showUnavailable={showUnavailable} unavailable={unavailable}
          disabled={disabled} hasSelection={selected.length > 0} canRecommend={recommended.length > 0 && !recommendedSelected}
          onGroup={setGroup} onQuery={setQuery} onShowUnavailable={setShowUnavailable}
          onRecommended={() => updateSelection(recommended)} onClear={() => updateSelection([])} />
        <div className="categories-heading"><span>{visible.length} of {state.categories.length} categories shown</span><span>File data</span></div>
        <ul className="category-list" aria-label="Cleanup categories" aria-busy={busy}>
          {visible.map(category => <CategoryRow key={category.id} category={category} disabled={disabled} analyzed={analyzed}
            detailsEnabled={connected && analyzed && !busy && pending === null}
            onSelect={checked => select(category.id, checked)} onDetails={() => { if (state.scan_id) setDetails({ scanId: state.scan_id, categoryId: category.id, title: category.title }) }} />)}
        </ul>
        {visible.length === 0 ? <div className="category-empty" role="status"><p>{!connected ? 'Loading cleanup categories…' : 'No matching categories'}</p>
          {connected ? <><span>Change the group, clear your search, or show unavailable categories.</span><Button variant="outline" onClick={() => { setGroup('all'); setQuery(''); setShowUnavailable(true) }}>Show all categories</Button></> : null}
        </div> : null}
      </section>
    </main>
    <div className="action-bar"><div><p>{busy ? status : ready ? `${formatBytes(selectedBytes)} selected` : `${selected.length} ${selected.length === 1 ? 'category' : 'categories'} selected`}</p><span>{busy ? `${state.progress.checked.toLocaleString('en-US')} items checked` : ready ? `${countFiles(selectedFiles)}${hiddenSelected > 0 ? ` · ${hiddenSelected} selected categories hidden by filters` : ''}` : hiddenSelected > 0 ? `${hiddenSelected} selected ${hiddenSelected === 1 ? 'category is' : 'categories are'} hidden by filters` : 'Analyze to see what can be removed.'}</span></div>{busy ? <Button variant="outline" disabled={!connected || pending !== null} onClick={() => void invoke('cancel')}>{pending === 'cancel' ? <LoaderCircle className="animate-spin" /> : <X />}{pending === 'cancel' ? 'Stopping…' : 'Stop'}</Button> : ready ? <Button disabled={!canClean} onClick={requestClean}><Trash2 />Clean selected<ArrowRight /></Button> : <Button disabled={disabled || selected.length === 0} onClick={analyze}>{pending === 'scan' ? <LoaderCircle className="animate-spin" /> : <Search />}Analyze<ArrowRight /></Button>}</div>
    <footer className="app-footer"><span>{status}</span><span>{state.version ? `v${state.version}` : 'Clearspace'}</span></footer>
    <FilesDialog selection={details} scanId={state.scan_id} onClose={() => setDetails(null)} />
    <Dialog open={reviewOpen} onOpenChange={value => { if (!value) setReview(null) }}><DialogContent className="confirm-dialog">
      <DialogHeader><DialogTitle>Remove {formatBytes(review?.bytes ?? 0)}?</DialogTitle><DialogDescription>{countFiles(review?.files ?? 0)} from the selected categories will be permanently deleted.</DialogDescription></DialogHeader>
      <p className="review-categories">{review?.titles.join(' · ')}</p>
      <DialogFooter><Button variant="outline" disabled={pending !== null} onClick={() => setReview(null)}>Cancel</Button><Button disabled={!canClean || pending !== null} onClick={() => void clean()}>{pending === 'clean' ? <LoaderCircle className="animate-spin" /> : <Trash2 />}Delete selected files</Button></DialogFooter>
    </DialogContent></Dialog>
  </div>
}

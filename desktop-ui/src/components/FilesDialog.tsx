import { useCallback, useEffect, useRef, useState } from 'react'
import { File, LoaderCircle } from 'lucide-react'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { countFiles, formatBytes } from '../format'
import type { FileItem } from '../types'

export interface FileSelection { scanId: string; categoryId: string; title: string }
interface Props { selection: FileSelection | null; scanId: string | null; onClose: () => void }

export function FilesDialog({ selection, scanId, onClose }: Props) {
  const [items, setItems] = useState<FileItem[]>([])
  const [total, setTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const open = selection !== null && selection.scanId === scanId

  const load = useCallback(async (requestedScan: string, requestedCategory: string, offset: number, token: number) => {
    if (!window.pywebview?.api) return
    setLoading(true)
    setError(null)
    try {
      const response = await window.pywebview.api.get_files(requestedScan, requestedCategory, offset)
      if (generation.current !== token) return
      if (response.ok) {
        setItems(previous => offset === 0 ? response.items : [...previous, ...response.items])
        setTotal(response.total)
        setNextOffset(response.next_offset)
      } else setError(response.error)
    } catch {
      if (generation.current === token) setError('Could not load the file list. Close this window and try again.')
    } finally { if (generation.current === token) setLoading(false) }
  }, [])

  const selectionScan = selection?.scanId
  const categoryId = selection?.categoryId
  useEffect(() => {
    generation.current += 1
    setItems([])
    setNextOffset(null)
    setTotal(0)
    setError(null)
    setLoading(false)
    if (open && selectionScan && categoryId) void load(selectionScan, categoryId, 0, generation.current)
    return () => { generation.current += 1 }
  }, [open, selectionScan, categoryId, load])

  return <Dialog open={open} onOpenChange={value => { if (!value) onClose() }}>
    <DialogContent className="files-dialog">
      <DialogHeader><DialogTitle>{selection?.title ?? 'Files'}</DialogTitle>
        <DialogDescription>Files found during this analysis. Paths are shown for review.</DialogDescription></DialogHeader>
      {error ? <p role="alert" className="inline-error">{error}</p> : null}
      <ul className="file-list" aria-label="Files found" aria-busy={loading}>
        {items.map(item => <li key={item.path}><File className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div><p title={item.name}>{item.name}</p><span title={item.path}>{item.path}</span></div>
          <span className="file-size">{formatBytes(item.bytes)}</span></li>)}
      </ul>
      {loading ? <p className="dialog-loading" role="status"><LoaderCircle className="size-4 animate-spin" />Loading files…</p> : null}
      {!loading && !error && items.length === 0 ? <p className="field-help">No files were found in this category.</p> : null}
      <DialogFooter className="files-dialog-footer"><span className="field-help">{countFiles(items.length)} shown{total > items.length ? ` of ${total.toLocaleString('en-US')}` : ''}</span>
        <div>{nextOffset !== null && selection ? <Button variant="outline" disabled={loading} onClick={() => void load(selection.scanId, selection.categoryId, nextOffset, generation.current)}>Load more</Button> : null}<Button variant="outline" onClick={onClose}>Close</Button></div>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

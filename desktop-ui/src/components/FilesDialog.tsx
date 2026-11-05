import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, File, LoaderCircle } from 'lucide-react'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { countFiles, formatBytes } from '../format'
import type { FileItem } from '../types'
import { locale, t } from '../i18n'

export interface FileSelection { scanId: string; categoryId: string; title: string }
interface Props { selection: FileSelection | null; scanId: string | null; onClose: () => void; onIncludedChange: (item: FileItem, included: boolean) => Promise<boolean> }

export function FilesDialog({ selection, scanId, onClose, onIncludedChange }: Props) {
  const [items, setItems] = useState<FileItem[]>([])
  const [total, setTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
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
      } else setError(t(response.error))
    } catch {
      if (generation.current === token) setError(t('Could not load the file list. Close this window and try again.'))
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
    setSaving(false)
    if (open && selectionScan && categoryId) void load(selectionScan, categoryId, 0, generation.current)
    return () => { generation.current += 1 }
  }, [open, selectionScan, categoryId, load])

  async function toggle(item: FileItem, included: boolean) {
    const token = generation.current
    setSaving(true)
    setError(null)
    try {
      const saved = await onIncludedChange(item, included)
      if (generation.current === token) {
        if (saved) setItems(previous => previous.map(value => value.id === item.id ? { ...value, included } : value))
        else setError(t('Could not update the file selection. Try again.'))
      }
    } catch {
      if (generation.current === token) setError(t('Could not update the file selection. Try again.'))
    } finally {
      if (generation.current === token) setSaving(false)
    }
  }

  return <Dialog open={open} onOpenChange={value => { if (!value) onClose() }}>
    <DialogContent className="files-dialog">
      <DialogHeader><DialogTitle>{selection ? t(selection.title) : t('Files')}</DialogTitle>
        <DialogDescription>{t('Files found during this analysis. Uncheck a file to keep it. Your choices apply only to this analysis.')}</DialogDescription></DialogHeader>
      {error ? <p role="alert" className="inline-error">{error}</p> : null}
      <ul className="file-list" aria-label={t('Files found')} aria-busy={loading}>
        {items.map(item => <li key={item.id} className={item.included ? '' : 'file-excluded'}>
          <span className="category-checkbox"><input type="checkbox" aria-label={t('Delete {name}', { name: item.name })} checked={item.included} disabled={loading || saving}
            onChange={event => void toggle(item, event.target.checked)} /><span aria-hidden="true"><Check className="size-3.5" /></span></span>
          <File className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div><p title={item.name}>{item.name}</p><span title={item.path}>{item.path}</span></div>
          <span className="file-size">{formatBytes(item.bytes)}</span></li>)}
      </ul>
      {loading ? <p className="dialog-loading" role="status"><LoaderCircle className="size-4 animate-spin" />{t('Loading files…')}</p> : null}
      {!loading && !error && items.length === 0 ? <p className="field-help">{t('No files were found in this category.')}</p> : null}
      <DialogFooter className="files-dialog-footer"><span className="field-help">{total > items.length ? t('{files} shown of {total}', { files: countFiles(items.length), total: total.toLocaleString(locale()) }) : t('{files} shown', { files: countFiles(items.length) })}</span>
        <div>{nextOffset !== null && selection ? <Button variant="outline" disabled={loading || saving} onClick={() => void load(selection.scanId, selection.categoryId, nextOffset, generation.current)}>{t('Load more')}</Button> : null}<Button variant="outline" onClick={onClose}>{t('Close')}</Button></div>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

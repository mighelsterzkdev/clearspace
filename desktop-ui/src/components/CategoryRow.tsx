import { AppWindow, Check, Eye, Files, Globe2, History, Package, Settings2, Trash2 } from 'lucide-react'
import { Button } from './ui/button'
import { countFiles, countItems, formatBytes, isHistoryAction, isNativeAction, isRecycleBin } from '../format'
import type { Category } from '../types'

interface Props {
  category: Category; disabled: boolean; analyzed: boolean; detailsEnabled: boolean;
  onSelect: (checked: boolean) => void; onDetails: () => void
}

export function CategoryRow({ category, disabled, analyzed, detailsEnabled, onSelect, onDetails }: Props) {
  const recycleBin = isRecycleBin(category)
  const native = isNativeAction(category)
  const history = isHistoryAction(category)
  const Icon = recycleBin ? Trash2 : history ? History : native ? Settings2 : category.group === 'Developer tools' || category.id === 'pip_cache' ? Package
    : category.group === 'Applications' ? AppWindow
    : category.group === 'Browsers' || /^(chrome|edge|firefox)_/.test(category.id) ? Globe2 : Files
  const id = `category-${category.id}`
  const scanned = analyzed && category.selected
  const size = !scanned || category.analysis_error ? '—' : history ? 'History cleanup' : category.size_known === false ? 'Managed by Windows' : formatBytes(category.bytes)
  let detail = category.selected ? 'Ready to analyze' : 'Not selected'
  if (!category.available) detail = 'Unavailable'
  else if (scanned && category.analysis_error) detail = 'Needs attention'
  else if (scanned) detail = native ? (category.actions || 0) > 0 ? 'Ready to clean' : 'Nothing to clean' : recycleBin ? countItems(category.files) : countFiles(category.files)
  return <li className={`category-row ${category.available ? '' : 'category-unavailable'}`}>
    <span className="category-checkbox">
      <input id={id} type="checkbox" checked={category.selected} disabled={disabled || !category.available}
        onChange={event => onSelect(event.target.checked)} aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`} />
      <span aria-hidden="true"><Check className="size-3.5" strokeWidth={3} /></span>
    </span>
    <Icon className="category-icon" aria-hidden="true" />
    <label htmlFor={id} className="category-copy">
      <span id={`${id}-title`} className="category-title">{category.title}</span>
      <span id={`${id}-description`} className="category-description">{category.reason || category.description}</span>
    </label>
    <div className="category-actions">
      <div className="category-metrics">
        <span>{size}</span>
        <span>{detail}</span>
      </div>
      {recycleBin || native ? <span className="category-preview-space" aria-hidden="true" /> : <Button variant="ghost" size="icon" aria-label={`View files for ${category.title}`} title="View files"
        disabled={!detailsEnabled || category.files === 0} onClick={onDetails}><Eye /></Button>}
    </div>
  </li>
}

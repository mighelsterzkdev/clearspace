import { Check, Eye, Files, Globe2, Package } from 'lucide-react'
import { Button } from './ui/button'
import { countFiles, formatBytes } from '../format'
import type { Category } from '../types'

interface Props {
  category: Category; disabled: boolean; analyzed: boolean; detailsEnabled: boolean;
  onSelect: (checked: boolean) => void; onDetails: () => void
}

export function CategoryRow({ category, disabled, analyzed, detailsEnabled, onSelect, onDetails }: Props) {
  const Icon = category.id === 'user_temp' ? Files : category.id === 'pip_cache' ? Package : Globe2
  const id = `category-${category.id}`
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
        <span>{analyzed && category.selected ? formatBytes(category.bytes) : '—'}</span>
        <span>{!category.available ? 'Unavailable' : analyzed && category.selected ? countFiles(category.files) : category.selected ? 'Ready to analyze' : 'Not selected'}</span>
      </div>
      <Button variant="ghost" size="icon" aria-label={`View files for ${category.title}`} title="View files"
        disabled={!detailsEnabled || category.files === 0} onClick={onDetails}><Eye /></Button>
    </div>
  </li>
}

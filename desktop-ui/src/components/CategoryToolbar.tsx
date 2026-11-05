import { CheckCheck } from 'lucide-react'
import { Button } from './ui/button'
import { t } from '../i18n'

interface Props {
  disabled: boolean; selected: number; available: number;
  onSelectAll: () => void; onClear: () => void
  query: string; order: 'group' | 'size'; onQuery: (value: string) => void; onOrder: (value: 'group' | 'size') => void
}

export function CategoryToolbar({ disabled, selected, available, onSelectAll, onClear, query, order, onQuery, onOrder }: Props) {
  return <div className="category-toolbar" aria-label={t('Category selection')}>
    <div className="category-filters">
      <input type="search" aria-label={t('Search categories')} placeholder={t('Search categories')} value={query} onChange={event => onQuery(event.target.value)} />
      <select aria-label={t('Sort categories')} value={order} onChange={event => onOrder(event.target.value === 'size' ? 'size' : 'group')}>
        <option value="group">{t('By group')}</option><option value="size">{t('Largest first')}</option>
      </select>
    </div>
    <div className="selection-tools">
      <Button variant="outline" size="sm" disabled={disabled || available === 0 || selected === available} onClick={onSelectAll}>
        <CheckCheck />{t('Select all')}
      </Button>
      <Button variant="ghost" size="sm" disabled={disabled || selected === 0} onClick={onClear}>{t('Clear selection')}</Button>
    </div>
    <span>{t('{selected} of {available} selected', { selected, available })}</span>
  </div>
}

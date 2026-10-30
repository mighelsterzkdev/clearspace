import { CheckCheck } from 'lucide-react'
import { Button } from './ui/button'
import { t } from '../i18n'

interface Props {
  disabled: boolean; selected: number; available: number;
  onSelectAll: () => void; onClear: () => void
}

export function CategoryToolbar({ disabled, selected, available, onSelectAll, onClear }: Props) {
  return <div className="category-toolbar" aria-label={t('Category selection')}>
    <div className="selection-tools">
      <Button variant="outline" size="sm" disabled={disabled || available === 0 || selected === available} onClick={onSelectAll}>
        <CheckCheck />{t('Select all')}
      </Button>
      <Button variant="ghost" size="sm" disabled={disabled || selected === 0} onClick={onClear}>{t('Clear selection')}</Button>
    </div>
    <span>{t('{selected} of {available} selected', { selected, available })}</span>
  </div>
}

import { CheckCheck } from 'lucide-react'
import { Button } from './ui/button'

interface Props {
  disabled: boolean; selected: number; available: number;
  onSelectAll: () => void; onClear: () => void
}

export function CategoryToolbar({ disabled, selected, available, onSelectAll, onClear }: Props) {
  return <div className="category-toolbar" aria-label="Category selection">
    <div className="selection-tools">
      <Button variant="outline" size="sm" disabled={disabled || available === 0 || selected === available} onClick={onSelectAll}>
        <CheckCheck />Select all
      </Button>
      <Button variant="ghost" size="sm" disabled={disabled || selected === 0} onClick={onClear}>Clear selection</Button>
    </div>
    <span>{selected} of {available} selected</span>
  </div>
}

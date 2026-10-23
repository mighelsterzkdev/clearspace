import { Check, Search, X } from 'lucide-react'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

interface Props {
  groups: string[]; group: string; query: string; showUnavailable: boolean; unavailable: number;
  disabled: boolean; hasSelection: boolean; canRecommend: boolean;
  onGroup: (value: string) => void; onQuery: (value: string) => void;
  onShowUnavailable: (value: boolean) => void; onRecommended: () => void; onClear: () => void
}

export function CategoryToolbar({ groups, group, query, showUnavailable, unavailable, disabled,
  hasSelection, canRecommend, onGroup, onQuery, onShowUnavailable, onRecommended, onClear }: Props) {
  return <div className="category-toolbar">
    <div className="category-filters">
      <Select value={group} onValueChange={onGroup}>
        <SelectTrigger className="group-filter h-10 w-48" aria-label="Category group"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All groups</SelectItem>
          {groups.map(item => <SelectItem key={item} value={item}>{item}</SelectItem>)}
        </SelectContent>
      </Select>
      <div className="category-search">
        <Search className="size-4" aria-hidden="true" />
        <Input className="h-10" aria-label="Search categories" placeholder="Search categories" value={query}
          onChange={event => onQuery(event.target.value)} maxLength={128} spellCheck={false} />
        {query ? <Button variant="ghost" size="icon" aria-label="Clear search" onClick={() => onQuery('')}><X /></Button> : null}
      </div>
    </div>
    <div className="selection-tools">
      <label className="unavailable-toggle">
        <span className="category-checkbox">
          <input type="checkbox" checked={showUnavailable} onChange={event => onShowUnavailable(event.target.checked)} />
          <span aria-hidden="true"><Check className="size-3.5" strokeWidth={3} /></span>
        </span>
        <span>Show unavailable{unavailable > 0 ? ` (${unavailable})` : ''}</span>
      </label>
      <div>
        <Button variant="ghost" size="sm" disabled={disabled || !canRecommend} onClick={onRecommended}
          title="Select recommended categories across all groups">Select recommended</Button>
        <Button variant="ghost" size="sm" disabled={disabled || !hasSelection} onClick={onClear}
          title="Clear the selection across all groups">Clear selection</Button>
      </div>
    </div>
  </div>
}

import { Check, CircleAlert, Clock3, Minus } from 'lucide-react'
import { countActions, countItems, formatBytes, isHistoryAction } from '../format'
import type { CleanupResult as Result } from '../types'

export function CleanupResult({ result }: { result: Result }) {
  const actions = result.actions || []
  const completed = result.actions_completed ?? actions.filter(action => action.status === 'completed').length
  const failed = result.actions_failed ?? actions.filter(action => action.status === 'failed').length
  const deferred = result.actions_deferred ?? actions.filter(action => action.status === 'deferred').length
  const errors = result.failed > 0 || failed > 0
  const warning = result.cancelled || errors || deferred > 0
  const title = result.cancelled ? 'Cleanup stopped' : errors ? 'Cleanup finished with errors' : deferred > 0 ? 'Cleanup finished with pending work' : 'Cleanup complete'
  const ResultIcon = deferred > 0 && !errors && !result.cancelled ? Clock3 : warning ? CircleAlert : Check
  const hasRemoved = result.removed > 0 || result.freed_bytes > 0 || actions.length === 0
  const unknownSize = actions.some(action => action.status === 'completed' && action.freed_bytes === null && !isHistoryAction({ native_action: action.category_id }))
  const actionSummary = completed > 0 ? `${countActions(completed)} completed` : deferred > 0 ? `${countActions(deferred)} pending` : failed > 0 ? `${countActions(failed)} failed` : `${countActions(0)} completed`
  return <section className="cleanup-result" aria-labelledby="result-heading" role="status">
    <div className={`result-icon ${warning ? 'result-icon-warning' : ''}`}>
      <ResultIcon className="size-5" />
    </div>
    <div>
      <h3 id="result-heading">{title}</h3>
      <p>
        {hasRemoved ? <><strong>{formatBytes(result.freed_bytes)}</strong> removed{result.removed > 0 || actions.length === 0 ? ` · ${countItems(result.removed)}` : ''}</> : actionSummary}
        {hasRemoved && completed > 0 ? ` · ${countActions(completed)} completed` : ''}
        {result.skipped > 0 ? ` · ${result.skipped.toLocaleString('en-US')} skipped` : ''}
        {result.failed > 0 ? ` · ${result.failed.toLocaleString('en-US')} could not be removed` : ''}
        {failed > 0 && (hasRemoved || completed > 0 || deferred > 0) ? ` · ${countActions(failed)} failed` : ''}
        {deferred > 0 && (hasRemoved || completed > 0) ? ` · ${countActions(deferred)} pending` : ''}
      </p>
      {hasRemoved ? <span>{unknownSize ? 'Known size only. Windows did not report the size of every cleanup action.' : deferred > 0 ? 'Known size only. Pending Windows cleanup is not included.' : 'Total size of deleted items. Disk free space may differ.'}</span> : unknownSize ? <span>Windows did not report how much space these actions removed.</span> : null}
      {actions.length > 0 ? <ul className="cleanup-action-list" aria-label="Cleanup action results">
        {actions.map(action => {
          const Icon = action.status === 'completed' ? Check : action.status === 'failed' ? CircleAlert : action.status === 'deferred' ? Clock3 : Minus
          const status = action.status === 'completed' ? 'Completed' : action.status === 'failed' ? 'Failed' : action.status === 'deferred' ? 'Pending' : 'Skipped'
          const detail = action.detail || (action.status === 'deferred' ? 'Windows maintenance is still running. Analyze again after it finishes.' : null)
          return <li key={action.category_id}>
            <Icon className={action.status === 'failed' || action.status === 'deferred' ? 'text-amber-300' : action.status === 'completed' ? 'text-emerald-400' : 'text-muted-foreground'} aria-hidden="true" />
            <div><p><strong>{action.title}</strong><span>{status}{action.freed_bytes !== null && action.freed_bytes > 0 ? ` · ${formatBytes(action.freed_bytes)}` : ''}</span></p>
              {detail ? <span>{detail}</span> : null}
            </div>
          </li>
        })}
      </ul> : null}
    </div>
  </section>
}

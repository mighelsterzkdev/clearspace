import { Check, CircleAlert, Clock3, Minus } from 'lucide-react'
import { countActions, countItems, formatBytes, isHistoryAction } from '../format'
import type { CleanupResult as Result } from '../types'
import { locale, t } from '../i18n'

export function CleanupResult({ result }: { result: Result }) {
  const actions = result.actions || []
  const completed = result.actions_completed ?? actions.filter(action => action.status === 'completed').length
  const failed = result.actions_failed ?? actions.filter(action => action.status === 'failed').length
  const deferred = result.actions_deferred ?? actions.filter(action => action.status === 'deferred').length
  const errors = result.failed > 0 || failed > 0
  const warning = result.cancelled || errors || deferred > 0
  const title = t(result.cancelled ? 'Cleanup stopped' : errors ? 'Cleanup finished with errors' : deferred > 0 ? 'Cleanup finished with pending work' : 'Cleanup complete')
  const ResultIcon = deferred > 0 && !errors && !result.cancelled ? Clock3 : warning ? CircleAlert : Check
  const hasRemoved = result.removed > 0 || result.freed_bytes > 0 || actions.length === 0
  const unknownSize = actions.some(action => action.status === 'completed' && action.freed_bytes === null && !isHistoryAction({ native_action: action.category_id }))
  const actionSummary = completed > 0 ? t('{actions} completed', { actions: countActions(completed) }) : deferred > 0 ? t('{actions} pending', { actions: countActions(deferred) }) : failed > 0 ? t('{actions} failed', { actions: countActions(failed) }) : t('{actions} completed', { actions: countActions(0) })
  return <section className="cleanup-result" aria-labelledby="result-heading" role="status">
    <div className={`result-icon ${warning ? 'result-icon-warning' : ''}`}>
      <ResultIcon className="size-5" />
    </div>
    <div>
      <h3 id="result-heading">{title}</h3>
      <p>
        {hasRemoved ? <><strong>{formatBytes(result.freed_bytes)}</strong> {t('removed')}{result.removed > 0 || actions.length === 0 ? ` · ${countItems(result.removed)}` : ''}</> : actionSummary}
        {hasRemoved && completed > 0 ? ` · ${t('{actions} completed', { actions: countActions(completed) })}` : ''}
        {result.skipped > 0 ? ` · ${t('{count} skipped', { count: result.skipped.toLocaleString(locale()) })}` : ''}
        {result.failed > 0 ? ` · ${t('{count} could not be removed', { count: result.failed.toLocaleString(locale()) })}` : ''}
        {failed > 0 && (hasRemoved || completed > 0 || deferred > 0) ? ` · ${t('{actions} failed', { actions: countActions(failed) })}` : ''}
        {deferred > 0 && (hasRemoved || completed > 0) ? ` · ${t('{actions} pending', { actions: countActions(deferred) })}` : ''}
      </p>
      {hasRemoved ? <span>{t(unknownSize ? 'Known size only. Windows did not report the size of every cleanup action.' : deferred > 0 ? 'Known size only. Pending Windows cleanup is not included.' : 'Total size of deleted items. Disk free space may differ.')}</span> : unknownSize ? <span>{t('Windows did not report how much space these actions removed.')}</span> : null}
      {actions.length > 0 ? <ul className="cleanup-action-list" aria-label={t('Cleanup action results')}>
        {actions.map(action => {
          const Icon = action.status === 'completed' ? Check : action.status === 'failed' ? CircleAlert : action.status === 'deferred' ? Clock3 : Minus
          const status = t(action.status === 'completed' ? 'Completed' : action.status === 'failed' ? 'Failed' : action.status === 'deferred' ? 'Pending' : 'Skipped')
          const detail = action.detail ? t(action.detail) : action.status === 'deferred' ? t('Windows maintenance is still running. Analyze again after it finishes.') : null
          return <li key={action.category_id}>
            <Icon className={action.status === 'failed' || action.status === 'deferred' ? 'text-warning' : action.status === 'completed' ? 'text-success' : 'text-muted-foreground'} aria-hidden="true" />
            <div><p><strong>{t(action.title)}</strong><span>{status}{action.freed_bytes !== null && action.freed_bytes > 0 ? ` · ${formatBytes(action.freed_bytes)}` : ''}</span></p>
              {detail ? <span>{detail}</span> : null}
            </div>
          </li>
        })}
      </ul> : null}
    </div>
  </section>
}

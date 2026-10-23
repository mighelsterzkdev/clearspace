import { Check, CircleAlert } from 'lucide-react'
import { countItems, formatBytes } from '../format'
import type { CleanupResult as Result } from '../types'

export function CleanupResult({ result }: { result: Result }) {
  const warning = result.cancelled || result.failed > 0
  const title = result.cancelled ? 'Cleanup stopped' : result.failed > 0 ? 'Cleanup finished with errors' : 'Cleanup complete'
  return <section className="cleanup-result" aria-labelledby="result-heading" role="status">
    <div className={`result-icon ${warning ? 'result-icon-warning' : ''}`}>
      {warning ? <CircleAlert className="size-5" /> : <Check className="size-5" />}
    </div>
    <div>
      <h3 id="result-heading">{title}</h3>
      <p>
        <strong>{formatBytes(result.freed_bytes)}</strong> removed · {countItems(result.removed)}
        {result.skipped > 0 ? ` · ${result.skipped.toLocaleString('en-US')} skipped` : ''}
        {result.failed > 0 ? ` · ${result.failed.toLocaleString('en-US')} could not be removed` : ''}
      </p>
      <span>Total size of deleted items. Disk free space may differ.</span>
    </div>
  </section>
}

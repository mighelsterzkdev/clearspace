import { useEffect, useState } from 'react'
import { Activity, ArrowRight, Check, CircleAlert, Globe, HardDrive, LoaderCircle, Power, RefreshCw, ShieldCheck, Square, Wrench, PackageCheck } from 'lucide-react'
import { Button } from './ui/button'
import { formatBytes } from '../format'
import { t } from '../i18n'
import type { ConnectionTarget, ToolsState, UpdatePackage } from '../types'

export interface ToolActions {
  flush_dns(): void; test_connection(target: ConnectionTarget): void; stop_connection(): void
  check_disks(): void; list_startup(): void; set_startup(id: string, enabled: boolean): void
  check_updates(): void; install_updates(ids: string[]): void; stop_updates(): void
  repair_windows(): void; restart_as_administrator(): void
}

interface Props { tools: ToolsState | undefined; connected: boolean; actions: ToolActions }

// Each tool is one card: what it does, one clear button, and the result in plain words.
export function ToolsPanel({ tools, connected, actions }: Props) {
  useEffect(() => {
    // Reading disk health and startup apps changes nothing, so both load when the tab opens.
    if (!connected || !tools) return
    if (tools.disks.status === 'idle') actions.check_disks()
    if (tools.startup.status === 'idle') actions.list_startup()
  }, [connected, tools?.disks.status, tools?.startup.status]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!tools) return <div className="category-empty" role="status"><p>{t('Loading tools…')}</p></div>
  return <section aria-labelledby="tools-heading" className="tools">
    <div className="section-heading"><div><h2 id="tools-heading">{t('Keep your PC in shape')}</h2><p>{t('Update programs, check the connection and repair Windows.')}</p></div></div>
    <UpdatesCard tools={tools} connected={connected} actions={actions} />
    <ConnectionCard tools={tools} connected={connected} actions={actions} />
    <div className="tool-card tool-row">
      <ToolHeading icon={Globe} title="Clear DNS cache" description="Helps when a site or game does not connect after a network change." />
      <div className="tool-side">
        {tools.dns.status === 'done' ? <span className="tool-ok"><Check className="size-4" aria-hidden="true" />{t('DNS cache cleared')}</span>
          : tools.dns.status === 'failed' ? <span className="tool-error">{t(tools.dns.error ?? '')}</span> : null}
        <Button variant="outline" disabled={!connected || tools.dns.status === 'running'} onClick={actions.flush_dns}>
          {tools.dns.status === 'running' ? <LoaderCircle className="animate-spin" /> : <Globe />}{t('Clear')}</Button>
      </div>
    </div>
    <RepairCard tools={tools} connected={connected} actions={actions} />
    <DisksCard tools={tools} connected={connected} actions={actions} />
    <StartupCard tools={tools} connected={connected} actions={actions} />
  </section>
}

function ToolHeading({ icon: Icon, title, description }: { icon: typeof Globe; title: string; description: string }) {
  return <div className="tool-heading"><span className="tool-icon"><Icon className="size-5" aria-hidden="true" /></span>
    <div><h3>{t(title)}</h3><p>{t(description)}</p></div></div>
}

function UpdatesCard({ tools, connected, actions }: Props & { tools: ToolsState }) {
  const updates = tools.updates
  const [chosen, setChosen] = useState<string[] | null>(null)
  const packages = updates.packages
  const pending = packages.filter(item => item.status === 'pending')
  const marked = (chosen ?? pending.map(item => item.id)).filter(id => pending.some(item => item.id === id))
  const working = updates.status === 'checking' || updates.status === 'updating'
  const listed = updates.status !== 'idle' && updates.status !== 'checking' && packages.length > 0
  const toggle = (id: string) => setChosen(marked.includes(id) ? marked.filter(item => item !== id) : [...marked, id])
  return <div className="tool-card">
    <div className="tool-row">
      <ToolHeading icon={PackageCheck} title="Update programs" description="Finds new versions of installed programs with Windows Package Manager and installs the ones you choose." />
      <div className="tool-side">
        {updates.status === 'updating' ? <Button variant="outline" disabled={!connected} onClick={actions.stop_updates}><Square />{t('Stop after this one')}</Button>
          : <Button variant="outline" disabled={!connected || working} onClick={() => { setChosen(null); actions.check_updates() }}>
            {updates.status === 'checking' ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{t(updates.status === 'checking' ? 'Checking…' : listed ? 'Check again' : 'Check for updates')}</Button>}
      </div>
    </div>
    {updates.status === 'failed' ? <p className="tool-error tool-body">{t(updates.error ?? '')}</p> : null}
    {updates.status !== 'idle' && updates.status !== 'checking' && updates.status !== 'failed' && packages.length === 0
      ? <p className="tool-ok tool-body"><Check className="size-4" aria-hidden="true" />{t('All programs are up to date.')}</p> : null}
    {listed ? <div className="tool-body">
      <ul className="package-list" aria-label={t('Available updates')}>
        {packages.map(item => <PackageRow key={item.id} item={item} marked={marked.includes(item.id)} current={updates.current === item.id}
          disabled={!connected || working || item.status !== 'pending'} onToggle={() => toggle(item.id)} />)}
      </ul>
      <div className="tool-footer"><span>{t('Some installers may ask for Windows permission.')}</span>
        <Button disabled={!connected || working || marked.length === 0} onClick={() => actions.install_updates(marked)}>
          {updates.status === 'updating' ? <LoaderCircle className="animate-spin" /> : <ArrowRight />}
          {t(marked.length === pending.length ? 'Update all ({count})' : 'Update selected ({count})', { count: marked.length })}</Button></div>
    </div> : null}
  </div>
}

function PackageRow({ item, marked, current, disabled, onToggle }: { item: UpdatePackage; marked: boolean; current: boolean; disabled: boolean; onToggle(): void }) {
  const status = current ? <span className="package-status"><LoaderCircle className="size-4 animate-spin" aria-hidden="true" />{t('Updating…')}</span>
    : item.status === 'updated' ? <span className="package-status tool-ok"><Check className="size-4" aria-hidden="true" />{t('Updated')}</span>
    : item.status === 'failed' ? <span className="package-status tool-error"><CircleAlert className="size-4" aria-hidden="true" />{t('Failed')}</span> : null
  return <li className="package-row">
    <label className="category-checkbox"><input type="checkbox" checked={marked && item.status === 'pending'} disabled={disabled} onChange={onToggle} aria-label={item.name} />
      <span><Check className="size-3.5" aria-hidden="true" /></span></label>
    <div className="package-copy"><p title={item.name}>{item.name}</p><span>{item.version} → {item.available}</span></div>
    {status}
  </li>
}

const verdicts: Record<string, { label: string; tone: string }> = {
  stable: { label: 'Stable connection', tone: 'tool-ok' }, unstable: { label: 'Unstable: the ping is jumping around', tone: 'tool-warning' },
  losing: { label: 'Losing packets', tone: 'tool-error' }, offline: { label: 'No answer from the server', tone: 'tool-error' },
}

function ConnectionCard({ tools, connected, actions }: Props & { tools: ToolsState }) {
  const connection = tools.connection
  const [target, setTarget] = useState<ConnectionTarget>('cloudflare')
  const running = connection.status === 'running'
  const summary = connection.summary
  const samples = connection.samples
  const highest = Math.max(50, ...samples.filter((value): value is number => value !== null))
  const verdict = summary?.verdict && !running ? verdicts[summary.verdict] : null
  return <div className="tool-card">
    <div className="tool-row">
      <ToolHeading icon={Activity} title="Test connection" description="Measures your ping for 30 seconds to show whether the internet is stable." />
      <div className="tool-side">
        <div className="target-chips" role="radiogroup" aria-label={t('Server')}>
          {(['cloudflare', 'google'] as const).map(value => <button key={value} type="button" role="radio" aria-checked={target === value} disabled={running}
            className="target-chip" onClick={() => setTarget(value)}>{value === 'cloudflare' ? 'Cloudflare' : 'Google'}</button>)}
        </div>
        {running ? <Button variant="outline" disabled={!connected} onClick={actions.stop_connection}><Square />{t('Stop')}</Button>
          : <Button variant="outline" disabled={!connected} onClick={() => actions.test_connection(target)}><Activity />{t('Test')}</Button>}
      </div>
    </div>
    {connection.status === 'failed' ? <p className="tool-error tool-body">{t(connection.error ?? '')}</p> : null}
    {samples.length > 0 ? <div className="tool-body">
      <svg className="ping-chart" viewBox={`0 0 ${60 * 6} 48`} preserveAspectRatio="none" role="img" aria-label={t('Ping over time')}>
        {samples.map((value, index) => value === null
          ? <rect key={index} x={index * 6} y={0} width={4} height={48} className="ping-lost" />
          : <rect key={index} x={index * 6} y={48 - Math.max(2, (value / highest) * 48)} width={4} height={Math.max(2, (value / highest) * 48)} className="ping-bar" />)}
      </svg>
      <dl className="ping-stats">
        <div><dt>{t('Average ping')}</dt><dd>{summary?.average != null ? `${summary.average} ms` : '—'}</dd></div>
        <div><dt>{t('Variation')}</dt><dd>{summary?.jitter != null ? `${summary.jitter} ms` : '—'}</dd></div>
        <div><dt>{t('Packet loss')}</dt><dd>{summary ? `${summary.loss}%` : '—'}</dd></div>
        <div><dt>{t('Result')}</dt><dd className={verdict?.tone}>{running ? t('Measuring… {count}/60', { count: samples.length }) : verdict ? t(verdict.label) : '—'}</dd></div>
      </dl>
    </div> : null}
  </div>
}

function RepairCard({ tools, connected, actions }: Props & { tools: ToolsState }) {
  const repair = tools.repair
  const running = repair.status === 'running'
  const result = repair.result
  const message = result ? {
    clean: ['tool-ok', 'No problems found in Windows system files.'],
    repaired: ['tool-ok', 'Damaged system files were repaired. Restart the PC to finish.'],
    unrepaired: ['tool-error', 'Some files could not be repaired. Run the repair again after restarting; if it repeats, Windows may need a repair install.'],
    failed: ['tool-error', 'The check could not finish. Restart the PC and try again.'],
  }[result.verdict] : null
  return <div className="tool-card">
    <div className="tool-row">
      <ToolHeading icon={Wrench} title="Repair Windows files" description="Runs DISM and System File Checker (sfc /scannow) to find and repair damaged system files." />
      <div className="tool-side">
        {tools.administrator ? <Button variant="outline" disabled={!connected || running} onClick={actions.repair_windows}>
          {running ? <LoaderCircle className="animate-spin" /> : <Wrench />}{t(running ? 'Repairing…' : 'Repair')}</Button>
          : <Button variant="outline" disabled={!connected} onClick={actions.restart_as_administrator}><ShieldCheck />{t('Restart as administrator')}</Button>}
      </div>
    </div>
    {!tools.administrator && !running ? <p className="field-help tool-body">{t('Repairing Windows needs administrator permission.')}</p> : null}
    {running ? <div className="tool-body operation-progress" role="status">
      <div><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /><span>{t(repair.phase === 'sfc' ? 'Step 2 of 2: checking system files' : 'Step 1 of 2: repairing the Windows image')}</span>
        <span>{repair.progress != null ? `${Math.round(repair.progress)}%` : ''}</span></div>
      {repair.progress != null ? <div className="progress-track"><span style={{ width: `${repair.progress}%` }} /></div> : <div className="indeterminate-track" aria-hidden="true"><span /></div>}
      <p className="field-help">{t('This can take 10 to 30 minutes. You can keep using the PC.')}</p>
    </div> : null}
    {repair.status === 'failed' ? <p className="tool-error tool-body">{t(repair.error ?? '')}</p> : null}
    {message && !running ? <p className={`${message[0]} tool-body`}>{message[0] === 'tool-ok' ? <Check className="size-4" aria-hidden="true" /> : <CircleAlert className="size-4" aria-hidden="true" />}{t(message[1])}</p> : null}
  </div>
}

function DisksCard({ tools, connected, actions }: Props & { tools: ToolsState }) {
  const disks = tools.disks
  const health: Record<string, [string, string]> = { healthy: ['tool-ok', 'Healthy'], warning: ['tool-warning', 'Warning'], unhealthy: ['tool-error', 'Failing'], unknown: ['', 'Unknown'] }
  return <div className="tool-card">
    <div className="tool-row">
      <ToolHeading icon={HardDrive} title="Disk health" description="What Windows reports about each SSD and hard drive." />
      <div className="tool-side"><Button variant="ghost" size="icon" aria-label={t('Check again')} title={t('Check again')} disabled={!connected || disks.status === 'running'} onClick={actions.check_disks}>
        {disks.status === 'running' ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}</Button></div>
    </div>
    {disks.status === 'failed' ? <p className="tool-error tool-body">{t(disks.error ?? '')}</p> : null}
    {disks.items.length ? <ul className="tool-body disk-list">
      {disks.items.map((disk, index) => <li key={index}>
        <div><p>{disk.name}</p><span>{[disk.size ? formatBytes(disk.size) : null, disk.media, disk.bus].filter(Boolean).join(' · ')}
          {disk.temperature != null ? ` · ${disk.temperature} °C` : ''}{disk.wear != null ? ` · ${t('{percent}% worn', { percent: disk.wear })}` : ''}</span></div>
        <span className={`disk-health ${health[disk.health][0]}`}>{t(health[disk.health][1])}</span>
      </li>)}
    </ul> : null}
  </div>
}

function StartupCard({ tools, connected, actions }: Props & { tools: ToolsState }) {
  const startup = tools.startup
  return <div className="tool-card">
    <div className="tool-row">
      <ToolHeading icon={Power} title="Startup apps" description="Programs that open with Windows. Turning one off makes startup faster; the program stays installed." />
      <div className="tool-side"><Button variant="ghost" size="icon" aria-label={t('Refresh list')} title={t('Refresh list')} disabled={!connected} onClick={actions.list_startup}><RefreshCw /></Button></div>
    </div>
    {startup.status === 'failed' ? <p className="tool-error tool-body">{t(startup.error ?? '')}</p> : null}
    {startup.items.length ? <ul className="tool-body startup-list" aria-label={t('Startup apps')}>
      {startup.items.map(item => {
        const locked = item.fixed || (item.scope === 'machine' && !tools.administrator)
        return <li key={item.id}>
          <div><p>{item.name}</p><span>{item.program}{item.fixed ? ` · ${t('Set by policy')}` : item.scope === 'machine' && !tools.administrator ? ` · ${t('All users: needs administrator')}` : ''}</span></div>
          <button type="button" role="switch" aria-checked={item.enabled} aria-label={item.name} className="switch" disabled={!connected || locked}
            onClick={() => actions.set_startup(item.id, !item.enabled)}><span /></button>
        </li>
      })}
    </ul> : startup.status === 'done' ? <p className="field-help tool-body">{t('No startup apps found.')}</p> : null}
  </div>
}

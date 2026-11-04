import { HardDrive } from 'lucide-react'
import { formatBytes } from '../format'
import { t } from '../i18n'

interface Props { disk: { drive: string; free: number; total: number }; reclaimable: number }

// The drive as one bar: used space, the part of it the current analysis can remove, and free space.
export function DiskOverview({ disk, reclaimable }: Props) {
  const used = Math.max(0, disk.total - disk.free)
  const share = (bytes: number) => `${Math.min(100, Math.max(0, bytes / disk.total * 100))}%`
  const removable = Math.min(reclaimable, used)
  return <section className="disk-overview" aria-label={t('Disk space')}>
    <div className="disk-overview-head">
      <span className="disk-overview-icon" aria-hidden="true"><HardDrive /></span>
      <div><span>{t('Disk {drive}', { drive: disk.drive })}</span><span>{t('{free} free of {total}', { free: formatBytes(disk.free), total: formatBytes(disk.total) })}</span></div>
      {removable > 0 ? <div className="disk-overview-gain"><span>{t('Can free')}</span><strong>{formatBytes(removable)}</strong></div> : null}
    </div>
    <div className="disk-overview-track" role="meter" aria-valuemin={0} aria-valuemax={disk.total} aria-valuenow={used} aria-label={t('Used space')}>
      <span className="disk-used" style={{ width: share(used - removable) }} />
      {removable > 0 ? <span className="disk-removable" style={{ width: share(removable) }} /> : null}
    </div>
    <ul className="disk-legend" aria-hidden="true">
      <li><i className="disk-used" />{t('Used')}</li>
      {removable > 0 ? <li><i className="disk-removable" />{t('Can free')}</li> : null}
      <li><i className="disk-free" />{t('Free')}</li>
    </ul>
  </section>
}

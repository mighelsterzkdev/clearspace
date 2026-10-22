const sizes = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), sizes.length - 1)
  const amount = value / 1024 ** unit
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: unit === 0 ? 0 : 1 }).format(amount)} ${sizes[unit]}`
}
export function countFiles(value: number): string { return `${value.toLocaleString('en-US')} ${value === 1 ? 'file' : 'files'}` }

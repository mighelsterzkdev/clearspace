import type { Category } from './types'

const sizes = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), sizes.length - 1)
  const amount = value / 1024 ** unit
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: unit === 0 ? 0 : 1 }).format(amount)} ${sizes[unit]}`
}
export function countFiles(value: number): string { return `${value.toLocaleString('en-US')} ${value === 1 ? 'file' : 'files'}` }
export function countItems(value: number): string { return `${value.toLocaleString('en-US')} ${value === 1 ? 'item' : 'items'}` }
export function countActions(value: number): string { return `${value.toLocaleString('en-US')} cleanup ${value === 1 ? 'action' : 'actions'}` }
export function isRecycleBin(category: Category): boolean { return category.native_action === 'empty_recycle_bin' || category.id === 'recycle_bin' }
export function isNativeAction(category: Category): boolean { return Boolean(category.native_action) && !isRecycleBin(category) }
export function isHistoryAction(category: Pick<Category, 'native_action'>): boolean { return ['run_history', 'explorer_search_history', 'recent_document_history'].includes(category.native_action || '') }

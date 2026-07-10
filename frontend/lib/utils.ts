/**
 * Small shared utilities.
 */

import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge conditional class names, resolving Tailwind conflicts
 * (later classes win, e.g. cn('p-2', 'p-4') === 'p-4').
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Human-friendly relative time, e.g. "just now", "5 minutes ago",
 * "3 days ago", falling back to a locale date beyond ~4 weeks.
 */
export function formatRelativeTime(date: Date | string | number): string {
  const d = date instanceof Date ? date : new Date(date)
  if (Number.isNaN(d.getTime())) return 'unknown'

  const diffMs = Date.now() - d.getTime()
  if (diffMs < 0) return d.toLocaleDateString()

  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 45) return 'just now'

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`

  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`

  const weeks = Math.floor(days / 7)
  if (weeks < 5) return `${weeks} week${weeks === 1 ? '' : 's'} ago`

  return d.toLocaleDateString()
}

/**
 * Format a byte count as a human-readable size, e.g. 10485760 -> "10 MB".
 */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`

  const units = ['KB', 'MB', 'GB', 'TB'] as const
  let value = bytes
  let unitIndex = -1
  do {
    value /= 1024
    unitIndex += 1
  } while (value >= 1024 && unitIndex < units.length - 1)

  const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10
  return `${rounded} ${units[unitIndex]}`
}

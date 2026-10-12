/**
 * Status / kind / severity style maps shared by the book dashboard panels.
 */

export const BOOK_STATUS_STYLES: Record<string, string> = {
  drafting: 'bg-yellow-100 text-yellow-700',
  revising: 'bg-blue-100 text-blue-700',
  complete: 'bg-green-100 text-green-700',
}

export const SCENE_STATUS_STYLES: Record<string, string> = {
  completed: 'bg-green-100 text-green-700',
  processing: 'bg-yellow-100 text-yellow-700',
  paused: 'bg-indigo-100 text-indigo-700',
  failed: 'bg-red-100 text-red-700',
}

export const THREAD_STATUS_STYLES: Record<string, string> = {
  open: 'bg-blue-100 text-blue-700',
  resolved: 'bg-green-100 text-green-700',
  abandoned: 'bg-gray-100 text-gray-600',
}

export const EVENT_KIND_STYLES: Record<string, string> = {
  setup: 'bg-purple-100 text-purple-700',
  development: 'bg-blue-100 text-blue-700',
  payoff: 'bg-green-100 text-green-700',
}

export const SEVERITY_STYLES: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 border-red-200',
  major: 'bg-orange-100 text-orange-700 border-orange-200',
  minor: 'bg-gray-100 text-gray-600 border-gray-200',
}

export const SEVERITY_ORDER: Record<string, number> = { critical: 0, major: 1, minor: 2 }

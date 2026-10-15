'use client'

/**
 * "⋯" overflow menu for a row's secondary actions (B4).
 *
 * At phone width a chapter row cannot fit every action next to its title, so
 * the rarer ones (generate planned scenes, history, delete) move in here. A
 * plain disclosure menu with the WAI-ARIA menu-button keyboard model: Enter /
 * Space / ArrowDown on the trigger opens it and focuses the first item,
 * ArrowUp / ArrowDown / Home / End move between items, Escape (or Tab, or a
 * click outside) closes it and Escape returns focus to the trigger.
 */

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { MoreHorizontal } from 'lucide-react'

export interface RowAction {
  key: string
  label: string
  icon?: ReactNode
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
}

export default function RowActionsMenu({
  label,
  actions,
  className,
}: {
  /** Accessible name of the trigger, e.g. "More actions for Onboarding". */
  label: string
  actions: RowAction[]
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])

  const enabled = actions
    .map((a, i) => ({ a, i }))
    .filter(({ a }) => !a.disabled)
    .map(({ i }) => i)

  const focusItem = useCallback((index: number) => {
    itemRefs.current[index]?.focus()
  }, [])

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false)
    if (returnFocus) triggerRef.current?.focus()
  }, [])

  // Focus the first enabled item when the menu opens.
  useEffect(() => {
    if (open && enabled.length > 0) focusItem(enabled[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // A click anywhere outside closes it (without stealing focus back).
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open, close])

  if (actions.length === 0) return null

  const onTriggerKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
    }
  }

  const onMenuKeyDown = (e: React.KeyboardEvent) => {
    const current = itemRefs.current.findIndex((el) => el === document.activeElement)
    const pos = enabled.indexOf(current)
    switch (e.key) {
      case 'Escape':
        e.preventDefault()
        e.stopPropagation()
        close(true)
        break
      case 'Tab':
        close(false)
        break
      case 'ArrowDown':
        e.preventDefault()
        focusItem(enabled[(pos + 1) % enabled.length])
        break
      case 'ArrowUp':
        e.preventDefault()
        focusItem(enabled[(pos - 1 + enabled.length) % enabled.length])
        break
      case 'Home':
        e.preventDefault()
        focusItem(enabled[0])
        break
      case 'End':
        e.preventDefault()
        focusItem(enabled[enabled.length - 1])
        break
    }
  }

  return (
    <div ref={rootRef} className={`relative ${className ?? ''}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        className="p-1.5 rounded-md text-gray-500 hover:text-primary-600 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-full z-20 mt-1 w-56 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
        >
          {actions.map((action, i) => (
            <button
              key={action.key}
              ref={(el) => {
                itemRefs.current[i] = el
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={action.disabled}
              onClick={() => {
                close(true)
                action.onSelect()
              }}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm focus:outline-none disabled:opacity-40 ${
                action.danger
                  ? 'text-red-600 hover:bg-red-50 focus:bg-red-50'
                  : 'text-gray-700 hover:bg-gray-100 focus:bg-gray-100'
              }`}
            >
              {action.icon}
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

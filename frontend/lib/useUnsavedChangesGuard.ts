/**
 * Unsaved-changes guard (SPEC-16, B1).
 *
 * `next/navigation` has no route-change event to veto, so the guard covers
 * the three ways out of a page separately:
 *
 * - **Tab close / reload / external URL**: a `beforeunload` handler while dirty.
 * - **In-app links** (Navbar, any `<a>`/`<Link>`): a capture-phase document
 *   click listener runs before React's handlers, asks `confirm()`, and on
 *   cancel stops the event so `<Link>` never navigates. No component that
 *   renders the link needs to know about the guard.
 * - **Browser Back**: while dirty one sentinel history entry (same URL, same
 *   Next.js state) is pushed; Back pops it, the `popstate` handler asks, and
 *   on cancel re-pushes it. On accept it steps back once more for real.
 *
 * Programmatic navigation must go through `guardedPush(href)` — a bare
 * `router.push` elsewhere on the page bypasses the guard.
 *
 * Once the sentinel exists, navigation away *replaces* it rather than pushing
 * past it, so the author's history never gains a phantom editor entry.
 */

'use client'

import { useCallback, useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'

export const UNSAVED_CHANGES_MESSAGE =
  'You have unsaved changes. Leave anyway? A local draft is kept and offered when you reopen this scene.'

export interface UnsavedChangesGuard {
  /** Navigate in-app, asking first when there are unsaved changes. Returns whether it navigated. */
  guardedPush: (href: string) => boolean
  /** Ask (only when dirty) whether to proceed with a destructive in-page action. */
  confirmLeave: () => boolean
}

export function useUnsavedChangesGuard(
  isDirty: boolean,
  message: string = UNSAVED_CHANGES_MESSAGE
): UnsavedChangesGuard {
  const router = useRouter()
  const dirtyRef = useRef(isDirty)
  dirtyRef.current = isDirty
  /** A sentinel history entry for this page is on top of the stack. */
  const armedRef = useRef(false)
  /** The author already agreed to leave; don't ask again on the way out. */
  const leavingRef = useRef(false)

  const confirmLeave = useCallback(
    () => !dirtyRef.current || leavingRef.current || window.confirm(message),
    [message]
  )

  const navigate = useCallback(
    (href: string) => {
      leavingRef.current = true
      if (armedRef.current) {
        armedRef.current = false
        router.replace(href)
      } else {
        router.push(href)
      }
    },
    [router]
  )

  const guardedPush = useCallback(
    (href: string) => {
      if (!confirmLeave()) return false
      navigate(href)
      return true
    },
    [confirmLeave, navigate]
  )

  // Tab close, reload, typed URL, cross-origin links.
  useEffect(() => {
    if (!isDirty) return
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (leavingRef.current) return
      event.preventDefault()
      // Legacy browsers need returnValue set to show the prompt.
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isDirty])

  // Browser Back: push the sentinel the first time the page turns dirty.
  useEffect(() => {
    if (!isDirty || armedRef.current) return
    try {
      // Copy Next's own history state: the app router reloads the page on a
      // popstate whose state it does not recognise.
      window.history.pushState(window.history.state, '', window.location.href)
      armedRef.current = true
    } catch {
      // history unavailable: beforeunload + link capture still apply
    }
  }, [isDirty])

  useEffect(() => {
    const onPopState = () => {
      // Only a pop off our sentinel concerns us.
      if (!armedRef.current || leavingRef.current) return
      if (dirtyRef.current && !window.confirm(message)) {
        try {
          window.history.pushState(window.history.state, '', window.location.href)
        } catch {
          armedRef.current = false
        }
        return
      }
      // Saved since (or agreed to leave): finish the Back the author pressed.
      armedRef.current = false
      leavingRef.current = true
      window.history.back()
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [message])

  // In-app links anywhere in the document (Navbar included).
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!dirtyRef.current && !armedRef.current) return
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return
      }
      const target = event.target as Element | null
      const anchor = target?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!anchor) return
      if ((anchor.target && anchor.target !== '_self') || anchor.hasAttribute('download')) return
      let url: URL
      try {
        url = new URL(anchor.href, window.location.href)
      } catch {
        return
      }
      // Cross-origin leaves the app: beforeunload asks.
      if (url.origin !== window.location.origin) return
      // Same page (e.g. a #hash link): nothing is lost.
      if (url.pathname === window.location.pathname && url.search === window.location.search) {
        return
      }
      event.preventDefault()
      event.stopPropagation()
      if (!confirmLeave()) return
      navigate(`${url.pathname}${url.search}${url.hash}`)
    }
    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  }, [confirmLeave, navigate])

  return { guardedPush, confirmLeave }
}

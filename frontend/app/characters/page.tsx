/**
 * Characters are book-scoped now (docs/ADR-002-book-as-root.md §1): a book's cast
 * lives in its Canon tab (open a book → Canon), editable and version-restorable.
 * The standalone characters page is retired; this route redirects stale links.
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Loading from '@/components/Loading'

export default function CharactersRedirectPage() {
  const router = useRouter()
  useEffect(() => {
    router.replace('/books')
  }, [router])
  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
      <Loading text="Characters live inside a book — taking you there…" />
    </div>
  )
}

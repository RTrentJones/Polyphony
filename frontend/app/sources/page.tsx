/**
 * Sources are managed inside a book now (open a book → Sources tab). There is no
 * top-level sources list, and no orphan-book upload path. This route redirects
 * any stale link to the books workspace.
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Loading from '@/components/Loading'

export default function SourcesRedirectPage() {
  const router = useRouter()
  useEffect(() => {
    router.replace('/books')
  }, [router])
  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
      <Loading text="Sources live inside a book — taking you there…" />
    </div>
  )
}

/**
 * The standalone source-detail page is retired — a source is viewed and edited in
 * its book (open the book → Sources tab). This route redirects stale links.
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Loading from '@/components/Loading'

export default function SourceDetailRedirectPage() {
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

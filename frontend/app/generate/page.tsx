/**
 * Standalone scene generation is gone — scenes are generated only inside a book
 * (open a book → Chapters → generate into a chapter). This route redirects any
 * stale link/bookmark to the books workspace.
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Loading from '@/components/Loading'

export default function GenerateRedirectPage() {
  const router = useRouter()
  useEffect(() => {
    router.replace('/books')
  }, [router])
  return (
    <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
      <Loading text="Scenes are generated inside a book — taking you there…" />
    </div>
  )
}

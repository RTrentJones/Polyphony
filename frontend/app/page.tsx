/**
 * Home Page - Redirects to Dashboard or Login
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/lib/store'
import Loading from '@/components/Loading'

export default function HomePage() {
  const router = useRouter()
  const { isAuthenticated, isLoading } = useAuthStore()

  useEffect(() => {
    if (!isLoading) {
      if (isAuthenticated) {
        router.push('/dashboard')
      } else {
        router.push('/auth/login')
      }
    }
  }, [isAuthenticated, isLoading, router])

  return <Loading fullScreen text="Loading..." />
}

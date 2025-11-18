/**
 * Protected Route Wrapper
 */

'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/lib/store'
import Loading from './Loading'

interface ProtectedRouteProps {
  children: React.ReactNode
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const router = useRouter()
  const { isAuthenticated, isLoading, loadUser } = useAuthStore()

  useEffect(() => {
    const checkAuth = async () => {
      try {
        await loadUser()
      } catch (error) {
        router.push('/auth/login')
      }
    }

    if (!isAuthenticated && !isLoading) {
      checkAuth()
    }
  }, [isAuthenticated, isLoading, loadUser, router])

  if (isLoading || !isAuthenticated) {
    return <Loading fullScreen text="Loading..." />
  }

  return <>{children}</>
}

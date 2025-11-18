/**
 * 404 Not Found Page
 */

import Link from 'next/link'
import Button from '@/components/Button'
import Card from '@/components/Card'
import { FileQuestion } from 'lucide-react'

export default function NotFound() {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <Card className="max-w-md text-center">
        <FileQuestion className="h-16 w-16 text-gray-400 mx-auto mb-4" />
        <h2 className="text-2xl font-bold text-gray-900 mb-2">
          Page Not Found
        </h2>
        <p className="text-gray-600 mb-6">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <Link href="/dashboard">
          <Button fullWidth>Go to Dashboard</Button>
        </Link>
      </Card>
    </div>
  )
}

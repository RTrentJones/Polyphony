/**
 * Dashboard Page
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, Users, Wand2, TrendingUp, Plus } from 'lucide-react'
import Card from '@/components/Card'
import Button from '@/components/Button'
import Loading from '@/components/Loading'
import { useManuscriptStore, useSceneStore } from '@/lib/store'

export default function DashboardPage() {
  const router = useRouter()
  const { manuscripts, isLoading: manuscriptsLoading, fetchManuscripts } = useManuscriptStore()
  const { scenes, isLoading: scenesLoading, fetchScenes } = useSceneStore()
  const [stats, setStats] = useState({
    totalManuscripts: 0,
    totalCharacters: 0,
    totalScenes: 0,
    recentActivity: 0,
  })

  useEffect(() => {
    const loadData = async () => {
      await Promise.all([
        fetchManuscripts(),
        fetchScenes(),
      ])
    }
    loadData()
  }, [fetchManuscripts, fetchScenes])

  useEffect(() => {
    if (manuscripts) {
      const totalCharacters = manuscripts.reduce(
        (sum, m) => sum + (m.character_count || 0),
        0
      )
      setStats({
        totalManuscripts: manuscripts.length,
        totalCharacters,
        totalScenes: scenes?.length || 0,
        recentActivity: manuscripts.filter(
          (m) =>
            new Date(m.created_at).getTime() >
            Date.now() - 7 * 24 * 60 * 60 * 1000
        ).length,
      })
    }
  }, [manuscripts, scenes])

  if (manuscriptsLoading || scenesLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading dashboard..." />
      </div>
    )
  }

  const statCards = [
    {
      title: 'Manuscripts',
      value: stats.totalManuscripts,
      icon: FileText,
      color: 'text-blue-600',
      bgColor: 'bg-blue-50',
      change: `${stats.recentActivity} this week`,
    },
    {
      title: 'Characters',
      value: stats.totalCharacters,
      icon: Users,
      color: 'text-green-600',
      bgColor: 'bg-green-50',
      change: 'Across all manuscripts',
    },
    {
      title: 'Scenes Generated',
      value: stats.totalScenes,
      icon: Wand2,
      color: 'text-purple-600',
      bgColor: 'bg-purple-50',
      change: 'Total scenes',
    },
  ]

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">Dashboard</h1>
        <p className="text-gray-600">
          Welcome back! Here's an overview of your creative projects.
        </p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        {statCards.map((stat) => {
          const Icon = stat.icon
          return (
            <Card key={stat.title} padding="md">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-600 mb-1">
                    {stat.title}
                  </p>
                  <p className="text-3xl font-bold text-gray-900">
                    {stat.value}
                  </p>
                  <p className="text-xs text-gray-500 mt-1">{stat.change}</p>
                </div>
                <div className={`p-3 rounded-lg ${stat.bgColor}`}>
                  <Icon className={`h-6 w-6 ${stat.color}`} />
                </div>
              </div>
            </Card>
          )
        })}
      </div>

      {/* Quick Actions */}
      <Card className="mb-8">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">
          Quick Actions
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Button
            variant="outline"
            onClick={() => router.push('/manuscripts')}
            className="justify-start"
          >
            <Plus className="h-5 w-5 mr-2" />
            Upload Manuscript
          </Button>
          <Button
            variant="outline"
            onClick={() => router.push('/generate')}
            className="justify-start"
          >
            <Wand2 className="h-5 w-5 mr-2" />
            Generate Scene
          </Button>
          <Button
            variant="outline"
            onClick={() => router.push('/manuscripts')}
            className="justify-start"
          >
            <FileText className="h-5 w-5 mr-2" />
            View Manuscripts
          </Button>
        </div>
      </Card>

      {/* Recent Manuscripts */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">
            Recent Manuscripts
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push('/manuscripts')}
          >
            View All
          </Button>
        </div>

        {!manuscripts || manuscripts.length === 0 ? (
          <div className="text-center py-12">
            <FileText className="h-12 w-12 text-gray-400 mx-auto mb-3" />
            <p className="text-gray-600 mb-4">No manuscripts yet</p>
            <Button onClick={() => router.push('/manuscripts')}>
              <Plus className="h-4 w-4 mr-2" />
              Upload Your First Manuscript
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            {manuscripts.slice(0, 5).map((manuscript) => (
              <div
                key={manuscript.id}
                onClick={() => router.push(`/manuscripts/${manuscript.id}`)}
                className="flex items-center justify-between p-4 rounded-lg border border-gray-200 hover:border-primary-300 hover:bg-primary-50 transition-colors cursor-pointer"
              >
                <div className="flex items-center space-x-4">
                  <div className="p-2 bg-primary-100 rounded-lg">
                    <FileText className="h-5 w-5 text-primary-600" />
                  </div>
                  <div>
                    <h3 className="font-medium text-gray-900">
                      {manuscript.title}
                    </h3>
                    <p className="text-sm text-gray-500">
                      {manuscript.character_count || 0} characters •{' '}
                      {new Date(manuscript.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <span
                    className={`px-2 py-1 text-xs font-medium rounded-full ${
                      manuscript.processing_status === 'completed'
                        ? 'bg-green-100 text-green-700'
                        : manuscript.processing_status === 'processing'
                        ? 'bg-yellow-100 text-yellow-700'
                        : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {manuscript.processing_status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

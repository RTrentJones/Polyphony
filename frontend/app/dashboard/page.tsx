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
import { useSourceStore, useSceneStore } from '@/lib/store'

export default function DashboardPage() {
  const router = useRouter()
  const { sources, isLoading: sourcesLoading, fetchSources } = useSourceStore()
  const { scenes, isLoading: scenesLoading, fetchScenes } = useSceneStore()
  const [stats, setStats] = useState({
    totalSources: 0,
    totalCharacters: 0,
    totalScenes: 0,
    recentActivity: 0,
  })

  useEffect(() => {
    const loadData = async () => {
      await Promise.all([
        fetchSources(),
        fetchScenes(),
      ])
    }
    loadData()
  }, [fetchSources, fetchScenes])

  useEffect(() => {
    if (sources) {
      const totalCharacters = sources.reduce(
        (sum, m) => sum + (m.character_count || 0),
        0
      )
      setStats({
        totalSources: sources.length,
        totalCharacters,
        totalScenes: scenes?.length || 0,
        recentActivity: sources.filter(
          (m) =>
            new Date(m.created_at).getTime() >
            Date.now() - 7 * 24 * 60 * 60 * 1000
        ).length,
      })
    }
  }, [sources, scenes])

  if (sourcesLoading || scenesLoading) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-4rem)]">
        <Loading size="lg" text="Loading dashboard..." />
      </div>
    )
  }

  const statCards = [
    {
      title: 'Sources',
      value: stats.totalSources,
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
      change: 'Across all sources',
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
            onClick={() => router.push('/sources')}
            className="justify-start"
          >
            <Plus className="h-5 w-5 mr-2" />
            Upload Source
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
            onClick={() => router.push('/sources')}
            className="justify-start"
          >
            <FileText className="h-5 w-5 mr-2" />
            View Sources
          </Button>
        </div>
      </Card>

      {/* Recent Sources */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">
            Recent Sources
          </h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push('/sources')}
          >
            View All
          </Button>
        </div>

        {!sources || sources.length === 0 ? (
          <div className="text-center py-12">
            <FileText className="h-12 w-12 text-gray-400 mx-auto mb-3" />
            <p className="text-gray-600 mb-4">No sources yet</p>
            <Button onClick={() => router.push('/sources')}>
              <Plus className="h-4 w-4 mr-2" />
              Upload Your First Source
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            {sources.slice(0, 5).map((source) => (
              <div
                key={source.id}
                onClick={() => router.push(`/sources/detail?id=${source.id}`)}
                className="flex items-center justify-between p-4 rounded-lg border border-gray-200 hover:border-primary-300 hover:bg-primary-50 transition-colors cursor-pointer"
              >
                <div className="flex items-center space-x-4">
                  <div className="p-2 bg-primary-100 rounded-lg">
                    <FileText className="h-5 w-5 text-primary-600" />
                  </div>
                  <div>
                    <h3 className="font-medium text-gray-900">
                      {source.title}
                    </h3>
                    <p className="text-sm text-gray-500">
                      {source.character_count || 0} characters •{' '}
                      {new Date(source.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <span
                    className={`px-2 py-1 text-xs font-medium rounded-full ${
                      source.processing_status === 'completed'
                        ? 'bg-green-100 text-green-700'
                        : source.processing_status === 'processing'
                        ? 'bg-yellow-100 text-yellow-700'
                        : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {source.processing_status}
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

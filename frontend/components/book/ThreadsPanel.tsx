/**
 * Threads panel — plot threads and their events.
 */

'use client'

import { useEffect, useState } from 'react'
import { GitBranch, Plus, Trash2 } from 'lucide-react'
import Button from '@/components/Button'
import Card from '@/components/Card'
import type { ToastType } from '@/components/Toast'
import { usePlanningStore } from '@/lib/books-store'
import type { BookChapter, PlotThread, ThreadEventKind } from '@/lib/types'
import { THREAD_STATUS_STYLES, EVENT_KIND_STYLES } from '@/components/book/styles'

function ThreadCard({
  bookId,
  thread,
  chapters,
  addToast,
}: {
  bookId: string
  thread: PlotThread
  chapters: BookChapter[]
  addToast: (message: string, type: ToastType) => void
}) {
  const { updateThread, deleteThread, addThreadEvent } = usePlanningStore()

  const [eventNote, setEventNote] = useState('')
  const [eventKind, setEventKind] = useState<ThreadEventKind>('development')
  const [eventChapterId, setEventChapterId] = useState('')
  const [addingEvent, setAddingEvent] = useState(false)

  const chapterTitle = (id: string | null) =>
    id ? chapters.find((c) => c.id === id)?.title ?? null : null

  const setStatus = async (status: 'open' | 'resolved' | 'abandoned') => {
    try {
      await updateThread(bookId, thread.id, { status })
    } catch (err: any) {
      addToast(err.message || 'Failed to update thread', 'error')
    }
  }

  const handleDelete = async () => {
    if (!confirm(`Delete thread "${thread.name}"?`)) return
    try {
      await deleteThread(bookId, thread.id)
      addToast('Thread deleted', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to delete thread', 'error')
    }
  }

  const handleAddEvent = async () => {
    if (!eventNote.trim()) return
    setAddingEvent(true)
    try {
      await addThreadEvent(bookId, thread.id, {
        note: eventNote.trim(),
        kind: eventKind,
        ...(eventChapterId ? { chapter_id: eventChapterId } : {}),
      })
      setEventNote('')
      setEventChapterId('')
      setEventKind('development')
    } catch (err: any) {
      addToast(err.message || 'Failed to add event', 'error')
    } finally {
      setAddingEvent(false)
    }
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          {thread.color && (
            <span
              className="h-3 w-3 rounded-full shrink-0 border border-gray-200"
              style={{ backgroundColor: thread.color }}
            />
          )}
          <h3 className="font-semibold text-gray-900 truncate">{thread.name}</h3>
          <span
            className={`px-2 py-0.5 text-xs font-medium rounded-full ${
              THREAD_STATUS_STYLES[thread.status] || 'bg-gray-100 text-gray-600'
            }`}
          >
            {thread.status}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {thread.status !== 'resolved' && (
            <Button variant="ghost" size="sm" onClick={() => setStatus('resolved')}>
              Resolve
            </Button>
          )}
          {thread.status !== 'open' && (
            <Button variant="ghost" size="sm" onClick={() => setStatus('open')}>
              Reopen
            </Button>
          )}
          {thread.status === 'open' && (
            <Button variant="ghost" size="sm" onClick={() => setStatus('abandoned')}>
              Abandon
            </Button>
          )}
          <button
            onClick={handleDelete}
            className="p-1 text-gray-400 hover:text-red-600"
            aria-label="Delete thread"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {thread.description && (
        <p className="text-sm text-gray-600 mb-3">{thread.description}</p>
      )}

      {/* Events */}
      {thread.events && thread.events.length > 0 && (
        <ul className="space-y-1.5 mb-3">
          {thread.events.map((event) => (
            <li key={event.id} className="flex items-start gap-2 text-sm">
              <span
                className={`px-2 py-0.5 text-xs font-medium rounded-full shrink-0 ${
                  EVENT_KIND_STYLES[event.kind] || 'bg-gray-100 text-gray-600'
                }`}
              >
                {event.kind}
              </span>
              <span className="text-gray-700">
                {event.note}
                {chapterTitle(event.chapter_id) && (
                  <span className="text-gray-400">
                    {' '}
                    — {chapterTitle(event.chapter_id)}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Add event */}
      <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-gray-100">
        <input
          value={eventNote}
          onChange={(e) => setEventNote(e.target.value)}
          placeholder="Add an event note..."
          className="flex-1 min-w-[10rem] px-3 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <select
          value={eventKind}
          onChange={(e) => setEventKind(e.target.value as ThreadEventKind)}
          className="px-2 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          <option value="setup">Setup</option>
          <option value="development">Development</option>
          <option value="payoff">Payoff</option>
        </select>
        <select
          value={eventChapterId}
          onChange={(e) => setEventChapterId(e.target.value)}
          className="px-2 py-1.5 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 max-w-[12rem]"
        >
          <option value="">No chapter</option>
          {chapters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.position + 1}. {c.title}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          onClick={handleAddEvent}
          isLoading={addingEvent}
          disabled={!eventNote.trim()}
        >
          <Plus className="h-3.5 w-3.5 mr-1" />
          Add
        </Button>
      </div>
    </div>
  )
}

export default function ThreadsPanel({
  bookId,
  chapters,
  addToast,
}: {
  bookId: string
  chapters: BookChapter[]
  addToast: (message: string, type: ToastType) => void
}) {
  const { threads, fetchThreads, createThread } = usePlanningStore()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    fetchThreads(bookId).catch((err: any) => {
      addToast(err.message || 'Failed to load threads', 'error')
    })
  }, [bookId, fetchThreads, addToast])

  const handleCreate = async () => {
    if (!name.trim()) return
    setCreating(true)
    try {
      await createThread(bookId, {
        name: name.trim(),
        ...(description.trim() ? { description: description.trim() } : {}),
      })
      setName('')
      setDescription('')
      addToast('Thread created', 'success')
    } catch (err: any) {
      addToast(err.message || 'Failed to create thread', 'error')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Card>
      <div className="mb-4">
        <h2 className="text-xl font-semibold text-gray-900">Plot Threads</h2>
        <p className="text-sm text-gray-500">
          Track setups, developments, and payoffs across the book
        </p>
      </div>

      {/* Add thread */}
      <div className="flex flex-wrap items-center gap-2 mb-6">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Thread name, e.g. The stolen locket"
          className="flex-1 min-w-[12rem] px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
          className="flex-1 min-w-[12rem] px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <Button size="sm" onClick={handleCreate} isLoading={creating} disabled={!name.trim()}>
          <Plus className="h-4 w-4 mr-1" />
          Add Thread
        </Button>
      </div>

      {threads.length === 0 ? (
        <div className="text-center py-8">
          <GitBranch className="h-12 w-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">No plot threads yet</p>
        </div>
      ) : (
        <div className="space-y-4">
          {threads.map((thread) => (
            <ThreadCard
              key={thread.id}
              bookId={bookId}
              thread={thread}
              chapters={chapters}
              addToast={addToast}
            />
          ))}
        </div>
      )}
    </Card>
  )
}

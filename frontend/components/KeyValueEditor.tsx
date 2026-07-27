/**
 * KeyValueEditor — edit a { key: value } map (character personality_traits,
 * voice_characteristics, relationships). Add/remove rows; emits a plain
 * Record<string, string> on every change. Blank-keyed rows are dropped on emit.
 */

'use client'

import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import Button from './Button'

type Row = { k: string; v: string }

function toRows(value: Record<string, unknown> | undefined): Row[] {
  if (!value) return []
  return Object.entries(value).map(([k, v]) => ({ k, v: String(v ?? '') }))
}

function toMap(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const { k, v } of rows) {
    const key = k.trim()
    if (key) out[key] = v
  }
  return out
}

export default function KeyValueEditor({
  label,
  value,
  onChange,
  keyPlaceholder = 'Key',
  valuePlaceholder = 'Value',
}: {
  label: string
  value: Record<string, unknown> | undefined
  onChange: (next: Record<string, string>) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
}) {
  const [rows, setRows] = useState<Row[]>(() => toRows(value))

  // Re-seed when the source object identity changes (e.g. a different character
  // loaded into the same modal).
  useEffect(() => {
    setRows(toRows(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const emit = (next: Row[]) => {
    setRows(next)
    onChange(toMap(next))
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <div className="space-y-2">
        {rows.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              className="w-1/3 px-2 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={row.k}
              placeholder={keyPlaceholder}
              onChange={(e) =>
                emit(rows.map((r, idx) => (idx === i ? { ...r, k: e.target.value } : r)))
              }
            />
            <input
              className="flex-1 px-2 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"
              value={row.v}
              placeholder={valuePlaceholder}
              onChange={(e) =>
                emit(rows.map((r, idx) => (idx === i ? { ...r, v: e.target.value } : r)))
              }
            />
            <button
              type="button"
              onClick={() => emit(rows.filter((_, idx) => idx !== i))}
              className="p-1.5 text-gray-400 hover:text-red-500"
              aria-label="Remove"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => emit([...rows, { k: '', v: '' }])}
        >
          <Plus className="h-3.5 w-3.5 mr-1" />
          Add {label.toLowerCase()}
        </Button>
      </div>
    </div>
  )
}

'use client'

/**
 * Side-by-side word diff of the current draft and an AI revision (SPEC-25).
 *
 * Word-level LCS with no dependency. The common prefix and suffix are peeled
 * off first (a revision changes one span and leaves the rest byte-identical),
 * so the table only covers the changed middle; a middle too large for the
 * table is shown as one deletion and one insertion rather than freezing the tab.
 */

export type DiffOp = 'equal' | 'insert' | 'delete'

export interface DiffPart {
  op: DiffOp
  text: string
}

/** Above this many LCS cells the middle is shown as a block replace. */
const MAX_CELLS = 4_000_000

/** Words and the whitespace between them, so joining the tokens restores the
 *  text exactly. */
export function tokenize(text: string): string[] {
  return text.split(/(\s+)/).filter((t) => t.length > 0)
}

function push(parts: DiffPart[], op: DiffOp, text: string) {
  if (!text) return
  const last = parts[parts.length - 1]
  if (last && last.op === op) last.text += text
  else parts.push({ op, text })
}

export function wordDiff(before: string, after: string): DiffPart[] {
  const a = tokenize(before)
  const b = tokenize(after)
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (
    tail < a.length - head &&
    tail < b.length - head &&
    a[a.length - 1 - tail] === b[b.length - 1 - tail]
  ) {
    tail++
  }
  const parts: DiffPart[] = []
  push(parts, 'equal', a.slice(0, head).join(''))

  const am = a.slice(head, a.length - tail)
  const bm = b.slice(head, b.length - tail)
  const n = am.length
  const m = bm.length
  if (n * m > MAX_CELLS) {
    push(parts, 'delete', am.join(''))
    push(parts, 'insert', bm.join(''))
  } else if (n > 0 || m > 0) {
    // lcs[i][j] = LCS length of am[i:] and bm[j:], flattened.
    const width = m + 1
    const lcs = new Uint32Array((n + 1) * width)
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i * width + j] =
          am[i] === bm[j]
            ? lcs[(i + 1) * width + j + 1] + 1
            : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < n && j < m) {
      if (am[i] === bm[j]) {
        push(parts, 'equal', am[i])
        i++
        j++
      } else if (lcs[(i + 1) * width + j] >= lcs[i * width + j + 1]) {
        push(parts, 'delete', am[i++])
      } else {
        push(parts, 'insert', bm[j++])
      }
    }
    while (i < n) push(parts, 'delete', am[i++])
    while (j < m) push(parts, 'insert', bm[j++])
  }

  push(parts, 'equal', a.slice(a.length - tail).join(''))
  return coalesce(parts)
}

/** One change per run: a bare space kept between two changed words would
 *  read as "~~smiled~~ stared ~~warmly~~ coldly"; folded in, it reads as
 *  "~~smiled warmly~~ stared coldly". */
function coalesce(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = []
  let del = ''
  let ins = ''
  const flush = () => {
    push(out, 'delete', del)
    push(out, 'insert', ins)
    del = ''
    ins = ''
  }
  parts.forEach((p, k) => {
    const inChange = del !== '' || ins !== ''
    const next = parts[k + 1]
    if (p.op === 'delete') del += p.text
    else if (p.op === 'insert') ins += p.text
    else if (inChange && next && next.op !== 'equal' && /^\s+$/.test(p.text)) {
      del += p.text
      ins += p.text
    } else {
      flush()
      push(out, 'equal', p.text)
    }
  })
  flush()
  return out
}

export default function DiffView({
  before,
  after,
  beforeLabel = 'Current draft',
  afterLabel = 'Proposed revision',
}: {
  before: string
  after: string
  beforeLabel?: string
  afterLabel?: string
}) {
  const parts = wordDiff(before, after)
  const changed = parts.some((p) => p.op !== 'equal')
  return (
    <div data-testid="diff-view">
      {!changed && (
        <p className="mb-2 text-sm text-gray-500">The revision is identical to the draft.</p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <section aria-label={beforeLabel} className="rounded-lg border border-gray-200">
          <h4 className="px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-gray-500 border-b border-gray-100">
            {beforeLabel}
          </h4>
          <p className="p-3 max-h-[50vh] overflow-y-auto text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
            {parts.map((p, i) =>
              p.op === 'insert' ? null : p.op === 'delete' ? (
                <del
                  key={i}
                  data-testid="diff-delete"
                  className="bg-red-100 text-red-800 line-through decoration-red-400"
                >
                  {p.text}
                </del>
              ) : (
                <span key={i}>{p.text}</span>
              )
            )}
          </p>
        </section>
        <section aria-label={afterLabel} className="rounded-lg border border-gray-200">
          <h4 className="px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-gray-500 border-b border-gray-100">
            {afterLabel}
          </h4>
          <p className="p-3 max-h-[50vh] overflow-y-auto text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
            {parts.map((p, i) =>
              p.op === 'delete' ? null : p.op === 'insert' ? (
                <ins
                  key={i}
                  data-testid="diff-insert"
                  className="bg-green-100 text-green-900 no-underline"
                >
                  {p.text}
                </ins>
              ) : (
                <span key={i}>{p.text}</span>
              )
            )}
          </p>
        </section>
      </div>
    </div>
  )
}

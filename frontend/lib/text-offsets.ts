/**
 * Offsets and fingerprints shared with the revise API (SPEC-19 / SPEC-25).
 *
 * A JS string index counts UTF-16 code units; the server's offsets are Python
 * string indices, i.e. Unicode code points. They differ after any character
 * outside the BMP (an emoji, some CJK): "😀" is 2 in JS and 1 in Python.
 *
 * The draft hash is the SHA-256 hex of the draft's UTF-8 bytes, the same as
 * app/orchestration/revise.text_hash, so a stale view is refused with a 409
 * instead of revising the wrong words.
 */

/** Code points in `text[0, utf16Index)`. A lone high surrogate split by the
 *  index counts as one code point (it cannot be split server-side). */
export function utf16ToCodePoint(text: string, utf16Index: number): number {
  const end = Math.max(0, Math.min(utf16Index, text.length))
  let count = 0
  for (let i = 0; i < end; i++) {
    const unit = text.charCodeAt(i)
    // A high surrogate followed by a low one is one code point.
    if (unit >= 0xd800 && unit <= 0xdbff && i + 1 < end) {
      const next = text.charCodeAt(i + 1)
      if (next >= 0xdc00 && next <= 0xdfff) i++
    }
    count++
  }
  return count
}

/** The UTF-16 index of code point `cpIndex` in `text` (clamped to the end). */
export function codePointToUtf16(text: string, cpIndex: number): number {
  let i = 0
  let cp = 0
  while (i < text.length && cp < cpIndex) {
    const unit = text.charCodeAt(i)
    if (unit >= 0xd800 && unit <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1)
      i += next >= 0xdc00 && next <= 0xdfff ? 2 : 1
    } else {
      i++
    }
    cp++
  }
  return i
}

/** The server's revise draft is the raw editable text (app/jobs/revise.py
 *  draft_of), edge whitespace included, so it is the editor text as is. */
export function serverDraft(text: string): string {
  return text || ''
}

export interface DraftSelection {
  /** Code-point offsets into the server draft, end exclusive. */
  start: number
  end: number
  /** The selected text, exactly as the server will slice it. */
  text: string
}

/**
 * A textarea selection (UTF-16, over the editor text) as a range into the
 * server draft (code points). Null when nothing, or only whitespace, is
 * selected.
 */
export function selectionToDraftRange(
  editorText: string,
  selectionStart: number,
  selectionEnd: number
): DraftSelection | null {
  const draft = serverDraft(editorText)
  const from = Math.max(0, Math.min(selectionStart, selectionEnd))
  const to = Math.min(draft.length, Math.max(selectionStart, selectionEnd))
  if (to <= from) return null
  const start = utf16ToCodePoint(draft, from)
  const end = utf16ToCodePoint(draft, to)
  if (end <= start) return null
  const text = draft.slice(codePointToUtf16(draft, start), codePointToUtf16(draft, end))
  if (!text.trim()) return null
  return { start, end, text }
}

/** SHA-256 hex of the UTF-8 bytes of `text` (Web Crypto; async). */
export async function sha256Hex(text: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) throw new Error('This browser cannot fingerprint the draft (no Web Crypto).')
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

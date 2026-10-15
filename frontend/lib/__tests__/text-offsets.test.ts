import { describe, expect, it } from 'vitest'

import {
  codePointToUtf16,
  selectionToDraftRange,
  sha256Hex,
  utf16ToCodePoint,
} from '@/lib/text-offsets'

describe('UTF-16 <-> code point offsets (SPEC-25)', () => {
  // "😀" is 2 UTF-16 units and 1 code point; "—" (em dash) is 1 of each.
  const text = 'Ada 😀 — then Bram left.'

  it('counts an emoji as one code point and an em dash as one', () => {
    const at = text.indexOf('then') // UTF-16 index
    expect(at).toBe(9)
    expect(utf16ToCodePoint(text, at)).toBe(8)
    expect(Array.from(text).slice(8, 12).join('')).toBe('then')
    expect(codePointToUtf16(text, 8)).toBe(9)
  })

  it('round-trips every index that is not inside a surrogate pair', () => {
    for (let cp = 0; cp <= Array.from(text).length; cp++) {
      expect(utf16ToCodePoint(text, codePointToUtf16(text, cp))).toBe(cp)
    }
  })

  it('turns a textarea selection into a code-point range of the raw draft', () => {
    // Edge whitespace is part of the draft (the server revises the raw text).
    const editor = `\n  ${text}\n\n`
    const from = editor.indexOf('then')
    const to = from + 'then Bram'.length
    const sel = selectionToDraftRange(editor, from, to)
    expect(sel).toEqual({ start: 11, end: 20, text: 'then Bram' })
    // What Python's draft[start:end] would slice:
    expect(Array.from(editor).slice(sel!.start, sel!.end).join('')).toBe(
      'then Bram'
    )
  })

  it('accepts a backwards selection and refuses an empty or all-whitespace one', () => {
    const from = text.indexOf('Bram')
    expect(selectionToDraftRange(text, from + 4, from)?.text).toBe('Bram')
    expect(selectionToDraftRange(text, 3, 3)).toBeNull()
    expect(selectionToDraftRange(`${text}\n\n`, text.length, text.length + 2)).toBeNull()
  })
})

describe('sha256Hex: the draft fingerprint (revise.text_hash)', () => {
  it('matches the FIPS 180-2 "abc" vector', async () => {
    expect(await sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    )
  })

  it('hashes the UTF-8 bytes, as Python does', async () => {
    // hashlib.sha256('é — 😀'.encode()).hexdigest()
    expect(await sha256Hex('é — 😀')).toBe(
      '203b8f0d06ad39d13a86c97fb6e76a370bfd01a52d652404beecb08cda223216'
    )
    expect(await sha256Hex('')).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    )
  })
})

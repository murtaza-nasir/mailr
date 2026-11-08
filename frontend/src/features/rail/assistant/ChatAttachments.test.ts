// ACT-SURFACES: web.panel
// What the paperclip, a paste and a drop accept before uploading (ACT agent.chat_attach).
import { describe, expect, it } from 'vitest'
import { filesOf, readable, sizeText } from './ChatAttachments'

describe('chat attachments', () => {
  it('accepts what the server reads, by type or by name', () => {
    expect(readable({ name: 'a.png', type: 'image/png' })).toBe(true)
    expect(readable({ name: 'scan', type: 'application/pdf' })).toBe(true)
    expect(readable({ name: 'notes.txt', type: 'text/plain' })).toBe(true)
    expect(readable({ name: 'plan.docx', type: '' })).toBe(true)
    expect(readable({ name: 'Budget.XLSX', type: 'application/octet-stream' })).toBe(true)
    expect(readable({ name: 'deck.odp', type: '' })).toBe(true)
    expect(readable({ name: 'letter.rtf', type: 'text/rtf' })).toBe(true)
    expect(readable({ name: 'IMG_1.heic', type: 'image/heic' })).toBe(false)
    expect(readable({ name: 'tool.exe', type: 'application/x-msdownload' })).toBe(false)
    expect(readable({ name: 'old.doc', type: 'application/msword' })).toBe(false)
  })

  it('names sizes plainly', () => {
    expect(sizeText(200)).toBe('1 KB')
    expect(sizeText(20_480)).toBe('20 KB')
    expect(sizeText(3 * 1048576)).toBe('3.0 MB')
  })

  it('takes files from a paste or a drop', () => {
    const f = new File(['x'], 'a.png', { type: 'image/png' })
    expect(filesOf(null)).toEqual([])
    expect(filesOf({ files: [f], items: [] } as unknown as DataTransfer)).toEqual([f])
    const item = { kind: 'file', getAsFile: () => f }
    expect(filesOf({ files: [], items: [item, { kind: 'string', getAsFile: () => null }] } as unknown as DataTransfer)).toEqual([f])
  })
})

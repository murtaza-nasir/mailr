// ACT-SURFACES: web.editor
// mail.insert_image: the rules behind the compose editor's inline images.
import { describe, expect, it } from 'vitest'
import {
  activePreset, alignFromElement, attachmentName, blobFromSrc, checkInlineFile, downscaleSize, dragWidth, imageStyle, initialSize,
  presetSize, shouldDownscale, stepSize, uploadSrc,
} from './inlineImages'

const el = (attrs: Record<string, string>) => ({ getAttribute: (n: string) => attrs[n] ?? null })
const photo = { width: 4000, height: 3000 }

describe('mail.insert_image limits', () => {
  it('takes PNG, JPEG, GIF and WebP, and never SVG', () => {
    for (const type of ['image/png', 'image/jpeg', 'image/gif', 'image/webp']) expect(checkInlineFile({ name: 'a', type, size: 10 }).ok).toBe(true)
    expect(checkInlineFile({ name: 'logo.svg', type: 'image/svg+xml', size: 10 })).toMatchObject({ ok: false, reason: expect.stringContaining('SVG') })
    expect(checkInlineFile({ name: 'logo.svg', type: '', size: 10 }).ok).toBe(false)
    expect(checkInlineFile({ name: 'a.tiff', type: 'image/tiff', size: 10 }).ok).toBe(false)
    expect(checkInlineFile({ name: 'a.pdf', type: 'application/pdf', size: 10 }).ok).toBe(false)
  })
})

describe('mail.insert_image downscale decision', () => {
  it('reduces images over 2560 px or 3 MB unless the message is set to full size', () => {
    expect(shouldDownscale({ ...photo, size: 1_000_000, type: 'image/jpeg' }, false)).toBe(true)
    expect(shouldDownscale({ width: 1200, height: 800, size: 4 * 1024 * 1024, type: 'image/png' }, false)).toBe(true)
    expect(shouldDownscale({ width: 1200, height: 800, size: 500_000, type: 'image/png' }, false)).toBe(false)
    expect(shouldDownscale({ width: 2560, height: 1440, size: 3 * 1024 * 1024, type: 'image/webp' }, false)).toBe(false)
    expect(shouldDownscale({ ...photo, size: 9_000_000, type: 'image/jpeg' }, true)).toBe(false)
  })
  it('never redraws a GIF (a canvas would drop its animation)', () => {
    expect(shouldDownscale({ ...photo, size: 9_000_000, type: 'image/gif' }, false)).toBe(false)
  })
  it('keeps the aspect ratio with the longer side at 2560', () => {
    expect(downscaleSize(4000, 3000)).toEqual({ width: 2560, height: 1920 })
    expect(downscaleSize(1000, 5000)).toEqual({ width: 512, height: 2560 })
    expect(downscaleSize(800, 600)).toEqual({ width: 800, height: 600 })
  })
})

describe('mail.insert_image resize commands', () => {
  it('inserts at the image width, at most 600 px', () => {
    expect(initialSize(photo)).toEqual({ width: 600, height: 450 })
    expect(initialSize({ width: 320, height: 200 })).toEqual({ width: 320, height: 200 })
  })
  it('Small, Medium and Original keep the proportions and never enlarge', () => {
    expect(presetSize('small', photo)).toEqual({ width: 200, height: 150 })
    expect(presetSize('medium', photo)).toEqual({ width: 400, height: 300 })
    expect(presetSize('original', photo)).toEqual(photo)
    expect(presetSize('medium', { width: 300, height: 100 })).toEqual({ width: 300, height: 100 })
    expect(activePreset(400, photo)).toBe('medium')
    expect(activePreset(512, photo)).toBeNull()
  })
  it('steps by a tenth with - and +, within the minimum and the editor width', () => {
    expect(stepSize(400, photo, 1)).toEqual({ width: 440, height: 330 })
    expect(stepSize(400, photo, -1)).toEqual({ width: 360, height: 270 })
    expect(stepSize(30, photo, -1).width).toBe(24)
    expect(stepSize(590, photo, 1, 600).width).toBe(600)
  })
  it('a corner drag grows towards that corner', () => {
    expect(dragWidth(300, 50, 'se')).toBe(350)
    expect(dragWidth(300, 50, 'ne')).toBe(350)
    expect(dragWidth(300, 50, 'nw')).toBe(250)
    expect(dragWidth(300, -50, 'sw')).toBe(350)
    expect(dragWidth(300, -400, 'se')).toBe(24)
    expect(dragWidth(300, 900, 'se', 640)).toBe(640)
  })
})

describe('mail.insert_image align commands', () => {
  it('writes alignment as margins that mail clients understand, and reads it back', () => {
    for (const a of ['left', 'center', 'right'] as const) {
      const style = imageStyle(a)
      expect(style).toContain('display:block')
      expect(alignFromElement(el({ style }))).toBe(a)
      expect(alignFromElement(el({ 'data-align': a }))).toBe(a)
    }
    expect(imageStyle(null)).toBe('max-width:100%;height:auto')
    expect(alignFromElement(el({ style: imageStyle(null) }))).toBeNull()
    expect(alignFromElement(el({}))).toBeNull()
  })
})

describe('mail.insert_image upload references', () => {
  it('recognises only upload sources', () => {
    const b = 'f'.repeat(32)
    expect(blobFromSrc(uploadSrc(b))).toBe(b)
    expect(blobFromSrc(`https://mail.example.com/api/v1/uploads/${b}`)).toBe(b)
    expect(blobFromSrc('https://cdn.example.com/a.png')).toBeNull()
    expect(blobFromSrc('data:image/png;base64,AAAA')).toBeNull()
    expect(blobFromSrc(`/api/v1/uploads/${b}/../x`)).toBeNull()
  })
  it('names an image made into an attachment after its alt text', () => {
    expect(attachmentName('image/jpeg', 'Q3 chart')).toBe('Q3 chart.jpg')
    expect(attachmentName('image/png', '')).toBe('image.png')
    expect(attachmentName('image/webp', 'a/b:c')).toBe('abc.webp')
  })
})

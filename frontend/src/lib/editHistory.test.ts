// The editor history against spec/cases/edit-history-cases.yaml, the vectors the
// Android twin (EditHistoryCasesTest) runs too: coalescing, step boundaries, the redo tail and the cap.
// ACT-SURFACES: web.note
import { describe, expect, it } from 'vitest'
import casesText from '../../../spec/cases/edit-history-cases.yaml?raw'
import androidCopy from '../../../android/app/src/test/resources/interaction/edit-history-cases.yaml?raw'
import { readFlowYaml } from './flowYaml'
import { CAP, EditHistory, IDLE_MS, caretAfter, classify, diff, isBigRemoval, reapplyText, revertText, type Step } from './editHistory'

const file = readFlowYaml(casesText)
type Ev = [string, ...unknown[]]

const show = (s: Step<string>) => (s.kind === 'text' ? [s.field, s.before, s.after] : ['op', s.label])

function play(c: Record<string, unknown>) {
  const h = new EditHistory<string>({ cap: (c.cap as number | undefined) ?? CAP })
  const big: number[] = []
  ;(c.do as Ev[]).forEach((e, i) => {
    switch (e[0]) {
      case 't': {
        const r = h.recordText(e[1] as string, e[2] as string, e[3] as string, e[4] as number, (e[5] as string | undefined) ?? null)
        if (r?.big) big.push(i)
        break
      }
      case 'op': h.recordOp(e[1] as string, e[1] as string); break
      case 'undo': h.undo(); break
      case 'redo': h.redo(); break
      case 'seal': h.seal(); break
      default: throw new Error(`unknown event ${e[0]}`)
    }
  })
  return { h, big }
}

describe('edit-history-cases.yaml (edit history)', () => {
  it('has the vectors and the Android copy matches', () => {
    expect(file.cases.length).toBeGreaterThanOrEqual(50)
    expect(new Set(file.cases.map((c) => c.id)).size).toBe(file.cases.length)
    expect(file.idle).toBe(IDLE_MS)
    expect(file.cap).toBe(CAP)
    expect(androidCopy).toBe(casesText)
  })

  for (const c of file.cases) {
    it(String(c.id), () => {
      const want = c.expect as Record<string, unknown> | string | boolean | null
      if (c.do) {
        const { h, big } = play(c)
        const w = want as { undo: unknown[]; redo: unknown[]; big?: number[] }
        expect(h.steps().map(show)).toEqual(w.undo)
        // Redo everything to read the redo stack in the order it would come back, newest undo first.
        const redone: unknown[] = []
        for (let s = h.redo(); s; s = h.redo()) redone.push(show(s))
        expect(redone).toEqual([...w.redo].reverse())
        if (w.big) expect(big).toEqual(w.big)
      } else if (c.classify) {
        const [b, a, t] = c.classify as [string, string, string | null]
        expect(classify(diff(b, a), t)).toBe(want)
      } else if (c.big) {
        const [b, a] = c.big as [string, string]
        expect(isBigRemoval(b, a)).toBe(want)
      } else if (c.revert) {
        const [cur, b, a] = c.revert as [string, string, string]
        expect(revertText(cur, { before: b, after: a })).toBe(want)
      } else if (c.reapply) {
        const [cur, b, a] = c.reapply as [string, string, string]
        expect(reapplyText(cur, { before: b, after: a })).toBe(want)
      } else throw new Error(`case ${String(c.id)} has no kind`)
    })
  }
})

describe('EditHistory', () => {
  it('keeps 200 steps by default and drops the oldest', () => {
    const h = new EditHistory<number>()
    for (let i = 0; i < 250; i++) h.recordOp(String(i), i)
    expect(h.undoSize).toBe(200)
    expect(h.steps()[0]).toMatchObject({ op: 50 })
  })

  it('undo and redo move one step at a time and report what they can do', () => {
    const h = new EditHistory<string>()
    expect(h.canUndo).toBe(false)
    h.recordText('body', '', 'a', 0)
    h.recordOp('Checked milk', 'x')
    expect(h.undo()).toMatchObject({ kind: 'op', label: 'Checked milk' })
    expect(h.canRedo).toBe(true)
    expect(h.undo()).toMatchObject({ kind: 'text', before: '', after: 'a' })
    expect(h.canUndo).toBe(false)
    expect(h.redo()).toMatchObject({ kind: 'text' })
    expect(h.redoSize).toBe(1)
  })

  it('drops a step that could not be applied', () => {
    const h = new EditHistory<string>()
    const s = h.recordOp('Deleted milk', 'x')
    h.undo()
    h.drop(s)
    expect(h.canRedo).toBe(false)
  })

  it('keeps text steps across a restore, without the operations', () => {
    const h = new EditHistory<string>()
    h.recordText('title', '', 'Groceries', 0, 'insertFromPaste')
    h.recordOp('Checked milk', 'x')
    h.recordText('body', 'a', 'ab', 5000)
    h.undo()
    const back = EditHistory.fromJSON<string>(JSON.parse(JSON.stringify(h)))
    expect(back.steps().map((s) => s.kind)).toEqual(['text'])
    expect(back.peekRedo()).toMatchObject({ field: 'body', before: 'a', after: 'ab', open: false })
  })

  it('puts the caret at the end of what came back', () => {
    expect(caretAfter('hello world', 'hello')).toBe(5)
    expect(caretAfter('hello', 'hello world')).toBe(11)
  })
})

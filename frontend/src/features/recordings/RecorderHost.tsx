// Where the recorder lives (recording.record): over every app, so a recording goes on while the user
// moves around mailr. The dialog and the minimised bar load only once the recorder is used.

import { lazy, Suspense } from 'react'
import { useRecorder } from './recorderStore'

const NewRecording = lazy(() => import('./NewRecording'))
const RecorderBar = lazy(() => import('./NewRecording').then((m) => ({ default: m.RecorderBar })))

export function RecorderHost() {
  const active = useRecorder((s) => s.dialog || s.phase !== 'idle')
  if (!active) return null
  return (
    <Suspense fallback={null}>
      <NewRecording />
      <RecorderBar />
    </Suspense>
  )
}

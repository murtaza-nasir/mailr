// Where the recording sheet and the browser recorder sit, over every app (AppShell). The sheet's
// code (RecordingSheet.tsx, with the notes, links and transcript views it shows) loads the first
// time a recording or its action items open; the first inbox paint does not need it. The recorder
// stays mounted on its own, so loading the sheet never restarts a recording in progress.

import { lazy, Suspense, useState } from 'react'
import { RecorderHost } from '../recordings/RecorderHost'
import { useRecordingSheet } from './store'

const RecordingSheetHost = lazy(() => import('./RecordingSheet').then((m) => ({ default: m.RecordingSheetHost })))

export function RecordingSheetSlot() {
  const wanted = useRecordingSheet((s) => s.open !== null || s.tasks !== null)
  const [used, setUsed] = useState(false)
  if (wanted && !used) setUsed(true)
  return (
    <>
      {(wanted || used) && (
        <Suspense fallback={null}>
          <RecordingSheetHost />
        </Suspense>
      )}
      <RecorderHost />
    </>
  )
}

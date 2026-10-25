// The microphone in the assistant's message box (web.panel): tap to speak, tap again to stop; the
// words land in the box for the user to read before sending. Shown only when the server offers
// dictation (features.transcribe).
// ACT agent.dictate @web.panel

import { Mic, Square } from 'lucide-react'
import { useEffect } from 'react'
import { toast } from '../../stores/ui'
import { useDictation, useTranscribeAvailable } from './useDictation'
import './ask.css'
import { t as tr } from '../../i18n'

export function MicButton({ onText, disabled, context }: { onText: (text: string) => void; disabled?: boolean; context?: string }) {
  const available = useTranscribeAvailable()
  const dict = useDictation({ onText: (t) => onText(t), context })
  const { error, setError } = dict
  useEffect(() => {
    if (!error) return
    toast({ message: error, error: true })
    setError(null)
  }, [error, setError])
  if (!available) return null
  const on = dict.phase === 'listening'
  const label = on ? tr('ask.micButton.stopDictation') : dict.phase === 'transcribing' ? tr('ask.micButton.transcribing') : tr('ask.micButton.dictate')
  return (
    <button
      type="button"
      className={`chat-mic${on ? ' on' : ''}${dict.phase === 'transcribing' ? ' busy' : ''}`}
      aria-label={label}
      aria-pressed={on}
      data-tip={label}
      data-testid="chat-mic"
      disabled={disabled || dict.phase === 'asking' || dict.phase === 'transcribing'}
      onClick={dict.toggle}
    >
      {on ? <Square size={14} /> : dict.phase === 'transcribing' ? <span className="spinner sm" aria-hidden /> : <Mic size={18} />}
    </button>
  )
}

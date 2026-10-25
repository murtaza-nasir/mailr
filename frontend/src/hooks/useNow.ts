// The current time, refreshed on an interval (due chips, the now line, Today's boundaries).
// One hook for Calendar, Tasks and the rail's calendar (three copies before).
import { useEffect, useState } from 'react'

export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}

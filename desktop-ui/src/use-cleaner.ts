import { useCallback, useEffect, useRef, useState } from 'react'
import { initialState, type CleanerApi, type CleanerState } from './types'

type Mutation = 'set_selection' | 'scan' | 'cancel' | 'clean' | 'open_search_settings'

export function useCleaner() {
  const [state, setState] = useState<CleanerState>(initialState)
  const [connected, setConnected] = useState(false)
  const [pending, setPending] = useState<Mutation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const alive = useRef(false)
  const occupied = useRef(false)
  const queued = useRef(0)
  const queue = useRef(Promise.resolve())

  useEffect(() => {
    alive.current = true
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    let failures = 0
    let polling = false
    const poll = async () => {
      if (stopped || polling) return
      polling = true
      const api = window.pywebview?.api
      if (api && !occupied.current && queued.current === 0) {
        occupied.current = true
        try {
          const response = await api.get_state()
          if (!stopped) {
            if (response.ok) { setState(response.state); setConnected(true); failures = 0 }
            else { setConnected(false); failures += 1 }
          }
        } catch {
          if (!stopped) { setConnected(false); failures += 1 }
        } finally { occupied.current = false }
      }
      polling = false
      if (!stopped) timer = setTimeout(poll, Math.min(400 * 2 ** failures, 4000))
    }
    const ready = () => { if (!polling) { clearTimeout(timer); void poll() } }
    window.addEventListener('pywebviewready', ready)
    void poll()
    return () => { stopped = true; alive.current = false; clearTimeout(timer); window.removeEventListener('pywebviewready', ready) }
  }, [])

  const invoke = useCallback(<K extends Mutation>(method: K, ...args: Parameters<CleanerApi[K]>): Promise<CleanerState | null> => {
    queued.current += 1
    setPending(method)
    setError(null)
    const execute = async () => {
      try {
        while (occupied.current && alive.current) await new Promise(resolve => setTimeout(resolve, 20))
        if (!alive.current || !window.pywebview?.api) return null
        occupied.current = true
        const operation = window.pywebview.api[method] as (...parameters: Parameters<CleanerApi[K]>) => ReturnType<CleanerApi[K]>
        const response = await operation(...args)
        if (!alive.current) return null
        if (response.ok) { setState(response.state); setConnected(true); return response.state }
        setError(response.error)
      } catch {
        if (alive.current) { setConnected(false); setError('The desktop connection was interrupted. Please try again.') }
      } finally {
        occupied.current = false
        queued.current -= 1
        if (alive.current && queued.current === 0) setPending(null)
      }
      return null
    }
    const operation = queue.current.then(execute, execute)
    queue.current = operation.then(() => undefined)
    return operation
  }, [])

  return { state, connected, pending, error, invoke, dismissError: () => setError(null) }
}

import { useEffect, useRef, useState } from 'react'
import './App.css'

function getSupportedMimeType() {
  const types = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg'
  ]
  for (const t of types) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t
  }
  return '' // Let browser choose default
}

function App() {
  const [recording, setRecording] = useState(false)
  const [partial, setPartial] = useState('')
  const [finalText, setFinalText] = useState('')
  const [error, setError] = useState('')

  const wsRef = useRef(null)
  const recRef = useRef(null)
  const streamRef = useRef(null)

  useEffect(() => {
    return () => {
      // Cleanup on unmount
      try { recRef.current && recRef.current.state !== 'inactive' && recRef.current.stop() } catch {}
      try { wsRef.current && wsRef.current.readyState === WebSocket.OPEN && wsRef.current.close() } catch {}
      try { streamRef.current && streamRef.current.getTracks().forEach(t => t.stop()) } catch {}
    }
  }, [])

  const start = async () => {
    setError('')
    setPartial('')
    setFinalText('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const ws = new WebSocket('ws://localhost:5000/ws/stt')
      ws.binaryType = 'arraybuffer'

      ws.onopen = () => {
        const mimeType = getSupportedMimeType()
        try {
          ws.send(JSON.stringify({ type: 'init', mimeType }))
        } catch {}
        const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
        rec.ondataavailable = async (ev) => {
          if (!ev.data || ev.data.size === 0) return
          try {
            const buf = await ev.data.arrayBuffer()
            ws.readyState === WebSocket.OPEN && ws.send(buf)
          } catch (e) { /* ignore */ }
        }
        rec.onerror = (e) => setError(`Recorder error: ${e.error?.message || e.message || 'unknown'}`)
        rec.start(300) // ~300ms chunks
        recRef.current = rec
        setRecording(true)
      }

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.type === 'result') {
            if (msg.final) {
              const text = msg?.result?.text || msg?.result?.partial || ''
              setFinalText(prev => (prev ? prev + ' ' : '') + text)
              setPartial('')
            } else {
              setPartial(msg?.result?.partial || '')
            }
          } else if (msg.type === 'error') {
            setError(msg?.message || 'Server error while decoding audio')
          } else if (msg.type === 'final') {
            const text = msg?.result?.text || ''
            setFinalText(prev => (prev ? prev + ' ' : '') + text)
            setPartial('')
          }
        } catch (e) {
          // Non-JSON or other server message
        }
      }

      ws.onerror = () => setError('WebSocket error. Is the backend running on ws://localhost:5000?')
      ws.onclose = () => setRecording(false)
      wsRef.current = ws
    } catch (e) {
      setError(e?.message || 'Could not access microphone')
    }
  }

  const stop = () => {
    try { recRef.current && recRef.current.state !== 'inactive' && recRef.current.stop() } catch {}
    try { wsRef.current && wsRef.current.readyState === WebSocket.OPEN && wsRef.current.send('final') } catch {}
    try { setTimeout(() => wsRef.current && wsRef.current.close(), 150) } catch {}
    try { streamRef.current && streamRef.current.getTracks().forEach(t => t.stop()) } catch {}
    setRecording(false)
  }

  return (
    <div style={{ maxWidth: 720, margin: '2rem auto', padding: '1rem' }}>
      <h1>Live Speech-to-Text (WebSocket)</h1>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        {!recording ? (
          <button onClick={start}>Start Mic</button>
        ) : (
          <button onClick={stop}>Stop</button>
        )}
        <span>Status: {recording ? 'Recording' : 'Idle'}</span>
      </div>

      {error && (
        <div style={{ color: 'crimson', marginTop: 12 }}>Error: {error}</div>
      )}

      <div style={{ marginTop: 20 }}>
        <h3>Partial</h3>
        <div style={{ minHeight: 28, padding: 8, background: '#f6f6f6', borderRadius: 6 }}>{partial}</div>
      </div>

      <div style={{ marginTop: 20 }}>
        <h3>Final Transcript</h3>
        <div style={{ minHeight: 48, padding: 8, background: '#eef7ff', borderRadius: 6, whiteSpace: 'pre-wrap' }}>{finalText}</div>
      </div>
    </div>
  )
}

export default App

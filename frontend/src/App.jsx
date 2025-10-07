import { useEffect, useRef, useState } from 'react'
import './App.css'

// Helper to downsample Float32 PCM to target sampleRate and return Float32Array
function downsampleBuffer(buffer, inSampleRate, outSampleRate) {
  if (outSampleRate === inSampleRate) return buffer
  const ratio = inSampleRate / outSampleRate
  const newLength = Math.round(buffer.length / ratio)
  const result = new Float32Array(newLength)
  let offsetResult = 0
  let offsetBuffer = 0
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio)
    // Simple average to reduce aliasing
    let accum = 0, count = 0
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i]
      count++
    }
    result[offsetResult] = count ? (accum / count) : 0
    offsetResult++
    offsetBuffer = nextOffsetBuffer
  }
  return result
}

// Convert Float32 PCM [-1,1] to Int16LE ArrayBuffer
function floatTo16LE(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2)
  const view = new DataView(buffer)
  let offset = 0
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, float32Array[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return buffer
}

function App() {
  const [recording, setRecording] = useState(false)
  const [partial, setPartial] = useState('')
  const [finalText, setFinalText] = useState('')
  const [error, setError] = useState('')

  const wsRef = useRef(null)
  const recRef = useRef(null) // unused in PCM mode
  const streamRef = useRef(null)
  const audioCtxRef = useRef(null)
  const srcRef = useRef(null)
  const workletRef = useRef(null)

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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true,
          channelCount: 1
        }
      })
      streamRef.current = stream

      const ws = new WebSocket('ws://localhost:5000/ws/stt')
      ws.binaryType = 'arraybuffer'

      ws.onopen = async () => {
        try {
          // Switch to raw PCM mode (16k, mono, s16le)
          ws.send(JSON.stringify({ type: 'init', mode: 'pcm', sampleRate: 16000 }))
        } catch {}

        // Build WebAudio graph and stream PCM
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)()
        audioCtxRef.current = audioCtx
        const source = audioCtx.createMediaStreamSource(stream)
        srcRef.current = source
        // Filters: HPF -> LPF -> Presence EQ -> DynamicsCompressor
        const hpf = audioCtx.createBiquadFilter(); hpf.type = 'highpass'; hpf.frequency.value = 120; hpf.Q.value = 0.707
        const lpf = audioCtx.createBiquadFilter(); lpf.type = 'lowpass'; lpf.frequency.value = 5000; lpf.Q.value = 0.707
        const eq = audioCtx.createBiquadFilter(); eq.type = 'peaking'; eq.frequency.value = 3000; eq.gain.value = 3; eq.Q.value = 1.0
        const comp = audioCtx.createDynamicsCompressor(); comp.threshold.value = -50; comp.knee.value = 30; comp.ratio.value = 3; comp.attack.value = 0.003; comp.release.value = 0.25
        const filterRefLocal = { hpf, lpf, eq, comp }
        
        // AudioWorklet: modern replacement for ScriptProcessor
        await audioCtx.audioWorklet.addModule('/pcm-worklet.js')
        const node = new AudioWorkletNode(audioCtx, 'pcm-processor', {
          processorOptions: { targetSampleRate: 16000, gateThresh: 0.015, gateHang: 8 }
        })
        workletRef.current = node
        let logged = 0
        node.port.onmessage = (ev) => {
          if (!ev || !ev.data) return
          if (ev.data.type === 'pcm' && ev.data.buffer && ws.readyState === WebSocket.OPEN) {
            if (logged < 5) { console.info('sending PCM bytes=', ev.data.buffer.byteLength); logged++ }
            ws.send(ev.data.buffer)
          }
        }

        // To ensure processor runs, connect to destination (audio will be inaudible)
        source.connect(hpf)
        hpf.connect(lpf)
        lpf.connect(eq)
        eq.connect(comp)
        comp.connect(node)
        // connect worklet to destination to keep the graph alive (audio is not audible)
        node.connect(audioCtx.destination)
        // Save filter for cleanup
        srcRef.current.__filter = filterRefLocal

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

      // Send a lightweight keepalive ping every 10s
      const ping = setInterval(() => {
        try { ws.readyState === WebSocket.OPEN && ws.send('ping') } catch {}
      }, 10000)

      ws.onerror = () => setError('WebSocket error. Is the backend running on ws://localhost:5000?')
      ws.onclose = (ev) => {
        clearInterval(ping)
        setRecording(false)
        if (!error) {
          setError(`WebSocket closed (${ev.code})`)
        }
      }
      wsRef.current = ws
    } catch (e) {
      setError(e?.message || 'Could not access microphone')
    }
  }

  const stop = () => {
    // Tear down WebAudio graph
    try { workletRef.current && workletRef.current.disconnect() } catch {}
    try { srcRef.current && srcRef.current.__filter && srcRef.current.__filter.disconnect() } catch {}
    try { srcRef.current && srcRef.current.disconnect() } catch {}
    try { audioCtxRef.current && audioCtxRef.current.state !== 'closed' && audioCtxRef.current.close() } catch {}
    workletRef.current = null
    srcRef.current = null
    audioCtxRef.current = null
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
        <div style={{ minHeight: 28, padding: 8, background: '#f6f6f6', borderRadius: 6, color: '#000', fontWeight: 'bold' }}>{partial}</div>
      </div>

      <div style={{ marginTop: 20 }}>
        <h3>Final Transcript</h3>
        <div style={{ minHeight: 48, padding: 8, background: '#eef7ff', borderRadius: 6, whiteSpace: 'pre-wrap', color: '#000', fontWeight: 'bold' }}>{finalText}</div>
      </div>
    </div>
  )
}

export default App

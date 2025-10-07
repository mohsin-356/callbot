class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.inRate = sampleRate; // provided by AudioWorklet global
    this.outRate = opts.targetSampleRate || 16000;
    this.gateThresh = typeof opts.gateThresh === 'number' ? opts.gateThresh : 0.015;
    this.gateHangTarget = Number.isInteger(opts.gateHang) ? opts.gateHang : 8;
    this.gateHang = 0;
  }

  downsample(buffer, inRate, outRate) {
    if (inRate === outRate) return buffer;
    const ratio = inRate / outRate;
    const newLength = Math.max(1, Math.round(buffer.length / ratio));
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      let accum = 0, count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
        accum += buffer[i];
        count++;
      }
      result[offsetResult] = count ? (accum / count) : 0;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  floatToInt16(float32Array) {
    const buffer = new ArrayBuffer(float32Array.length * 2);
    const view = new DataView(buffer);
    let offset = 0;
    for (let i = 0; i < float32Array.length; i++, offset += 2) {
      let s = Math.max(-1, Math.min(1, float32Array[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const ch0 = input[0];
    if (!ch0 || ch0.length === 0) return true;

    // Simple RMS gate with hangover
    let sum = 0;
    for (let i = 0; i < ch0.length; i++) { const v = ch0[i]; sum += v * v; }
    const rms = Math.sqrt(sum / ch0.length);
    if (rms >= this.gateThresh) {
      this.gateHang = this.gateHangTarget;
    } else if (this.gateHang > 0) {
      this.gateHang--;
    }
    if (rms < this.gateThresh && this.gateHang === 0) {
      return true; // drop quiet/non-speech
    }

    const down = this.downsample(ch0, this.inRate, this.outRate);
    if (down.length > 0) {
      const buf = this.floatToInt16(down);
      // Transfer ArrayBuffer to main thread for WebSocket send
      this.port.postMessage({ type: 'pcm', buffer: buf }, [buf]);
    }
    return true;
  }
}

registerProcessor('pcm-processor', PCMWorkletProcessor);

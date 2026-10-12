// Listens through the microphone during a voice conversation. It notices when someone starts and
// stops speaking and hands over each spoken sentence as a 16 kHz mono WAV; the server transcribes it.
// Only the loudness of the audio is examined here, against a noise floor that adapts to the room.

const TARGET_RATE = 16_000;
const FRAME_SECONDS = 0.02;
const START_FRAMES = 3; // about 60 ms of voice starts a sentence
const END_FRAMES = 45; // about 900 ms of quiet ends it
const PREROLL_FRAMES = 15; // the 300 ms before speech is kept, so the first syllable isn't lost
const MIN_VOICED_FRAMES = 20; // less than about 0.4 s of voice is treated as noise
const MAX_FRAMES = 45 / FRAME_SECONDS; // a sentence is cut at 45 s
const RESUME_DELAY_MS = 300; // lets the assistant's voice die away before listening again
const IDLE_MS = 120_000; // two quiet minutes end the conversation

// Copies microphone samples to the page. Loaded from a Blob URL, so no bundler setup is needed.
const CAPTURE_WORKLET = `
class VoiceCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor('voice-capture', VoiceCapture);
`;

export function microphoneProblem(error) {
  if (!navigator.mediaDevices?.getUserMedia) return 'Voice needs a secure page (HTTPS) and a browser with microphone access.';
  if (error?.name === 'NotAllowedError') return 'Microphone access is blocked. Allow it for this site to talk with the assistant.';
  if (error?.name === 'NotFoundError') return 'No microphone was found.';
  if (error?.message === 'audio-blocked') return 'The browser blocked audio. Click the microphone button again.';
  return 'The microphone could not start.';
}

function downsample(samples, rate) {
  if (rate <= TARGET_RATE) return { samples, rate };
  const ratio = rate / TARGET_RATE;
  const result = new Float32Array(Math.floor(samples.length / ratio));
  for (let index = 0; index < result.length; index += 1) {
    // Averaging each window also filters out frequencies the lower rate can't hold.
    const start = Math.floor(index * ratio);
    const end = Math.min(samples.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let position = start; position < end; position += 1) sum += samples[position];
    result[index] = sum / Math.max(1, end - start);
  }
  return { samples: result, rate: TARGET_RATE };
}

function wavBlob(samples, rate) {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset, value) => { for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([view.buffer], { type: 'audio/wav' });
}

export class VoiceListener {
  state = 'off'; // off | listening | hearing | paused
  #listeners = new Set();
  #stream = null;
  #context = null;
  #node = null;
  #pending = new Float32Array(0);
  #frameSize = 320;
  #noise = 0.004;
  #preroll = [];
  #sentence = null;
  #voicedRun = 0;
  #voicedFrames = 0;
  #quietRun = 0;
  #paused = false;
  #resumeAt = 0;
  #lastActivity = 0;

  // Events: { type: 'state', state } | { type: 'level', level } (0–1) | { type: 'sentence', wav } | { type: 'idle' }
  on(listener) {
    this.#listeners.add(listener);
    return () => { this.#listeners.delete(listener); };
  }

  #emit(event) {
    for (const listener of this.#listeners) listener(event);
  }

  #setState(state) {
    if (this.state === state) return;
    this.state = state;
    this.#emit({ type: 'state', state });
  }

  // Call from a click: the browser asks for the microphone here.
  async start() {
    if (this.state !== 'off') return;
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('insecure');
    // Created and resumed before any await, while the click still counts: Firefox and Safari keep
    // an audio context suspended otherwise, and the permission prompt can take a while.
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContext();
    const resumed = context.resume().catch(() => {});
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (error) {
      context.close().catch(() => {});
      throw error;
    }
    try {
      const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'application/javascript' }));
      try { await context.audioWorklet.addModule(url); } finally { URL.revokeObjectURL(url); }
      const node = new AudioWorkletNode(context, 'voice-capture');
      node.port.onmessage = (event) => this.#samples(event.data);
      context.createMediaStreamSource(stream).connect(node);
      node.connect(context.destination); // silent, but keeps the node running
      // Opening the audio device can take a couple of seconds (Firefox); never resuming means blocked.
      await Promise.race([resumed, new Promise((resolve) => setTimeout(resolve, 5000))]);
      if (context.state !== 'running') throw new Error('audio-blocked');
      this.#stream = stream;
      this.#context = context;
      this.#node = node;
    } catch (error) {
      stream.getTracks().forEach((track) => track.stop());
      context.close().catch(() => {});
      throw error;
    }
    this.#frameSize = Math.round(this.#context.sampleRate * FRAME_SECONDS);
    this.#reset();
    this.#paused = false;
    this.#lastActivity = performance.now();
    this.#setState('listening');
  }

  // While paused, microphone audio is dropped. Resuming waits briefly for echoes to fade.
  setPaused(paused) {
    if (this.state === 'off') return;
    if (paused) {
      this.#paused = true;
      this.#reset();
      this.#setState('paused');
    } else if (this.#paused) {
      this.#paused = false;
      this.#resumeAt = performance.now() + RESUME_DELAY_MS;
      this.#lastActivity = performance.now();
      this.#setState('listening');
    }
  }

  stop() {
    if (this.state === 'off') return;
    if (this.#node) this.#node.port.onmessage = null;
    this.#node?.disconnect();
    this.#stream?.getTracks().forEach((track) => track.stop());
    this.#context?.close().catch(() => {});
    this.#node = this.#stream = this.#context = null;
    this.#reset();
    this.#setState('off');
  }

  #reset() {
    this.#pending = new Float32Array(0);
    this.#preroll = [];
    this.#sentence = null;
    this.#voicedRun = this.#voicedFrames = this.#quietRun = 0;
  }

  #samples(block) {
    if (this.state === 'off' || this.#paused || performance.now() < this.#resumeAt) return;
    const joined = new Float32Array(this.#pending.length + block.length);
    joined.set(this.#pending);
    joined.set(block, this.#pending.length);
    let offset = 0;
    while (joined.length - offset >= this.#frameSize && !this.#paused) {
      this.#frame(joined.subarray(offset, offset + this.#frameSize).slice());
      offset += this.#frameSize;
    }
    this.#pending = this.#paused ? new Float32Array(0) : joined.slice(offset);
  }

  #frame(frame) {
    let sum = 0;
    for (let index = 0; index < frame.length; index += 1) sum += frame[index] * frame[index];
    const loudness = Math.sqrt(sum / frame.length);
    const voiced = loudness > Math.max(this.#noise * 3, 0.01);
    this.#emit({ type: 'level', level: Math.min(1, loudness / 0.12) });

    if (!this.#sentence) {
      if (!voiced) this.#noise = this.#noise * 0.95 + loudness * 0.05;
      this.#preroll.push(frame);
      if (this.#preroll.length > PREROLL_FRAMES) this.#preroll.shift();
      this.#voicedRun = voiced ? this.#voicedRun + 1 : 0;
      if (this.#voicedRun >= START_FRAMES) {
        this.#sentence = this.#preroll;
        this.#preroll = [];
        this.#voicedFrames = this.#voicedRun;
        this.#quietRun = 0;
        this.#lastActivity = performance.now();
        this.#setState('hearing');
      } else if (performance.now() - this.#lastActivity > IDLE_MS) {
        this.#lastActivity = performance.now();
        this.#emit({ type: 'idle' });
      }
      return;
    }
    this.#sentence.push(frame);
    if (voiced) {
      this.#voicedFrames += 1;
      this.#quietRun = 0;
    } else {
      this.#quietRun += 1;
    }
    if (this.#quietRun >= END_FRAMES || this.#sentence.length >= MAX_FRAMES) this.#finish();
  }

  #finish() {
    const frames = this.#sentence;
    const voicedFrames = this.#voicedFrames;
    this.#sentence = null;
    this.#voicedRun = this.#voicedFrames = this.#quietRun = 0;
    if (voicedFrames < MIN_VOICED_FRAMES) {
      this.#setState('listening');
      return;
    }
    // Wait for the transcript and the reply; the page resumes listening with setPaused(false).
    this.#paused = true;
    this.#setState('paused');
    const samples = new Float32Array(frames.length * this.#frameSize);
    frames.forEach((frame, index) => samples.set(frame, index * this.#frameSize));
    const audio = downsample(samples, this.#context.sampleRate);
    this.#emit({ type: 'sentence', wav: wavBlob(audio.samples, audio.rate) });
  }
}

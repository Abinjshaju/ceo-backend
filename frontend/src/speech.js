// Reads assistant replies aloud. The server decides what to read (the opening of the reply) and
// streams Gemini's voice as raw PCM, which plays while it arrives; the first words are heard about
// a second after the request. The browser's own voice takes over if Gemini speech is unavailable.
// stop() silences everything at once and cancels the stream, which also stops generation.
import { api, fetchStream } from './api.js';

// Schedule audio in pieces of at least a quarter second (24 kHz, 16-bit) to keep playback smooth.
const MIN_PIECE_BYTES = 12_000;

export class Speaker {
  state = 'idle'; // idle | preparing | speaking
  lang = 'en-US';
  #run = 0;
  #controller = null;
  #context = null;
  #sources = new Set();
  #settle = null;
  #listeners = new Set();

  subscribe(listener) {
    this.#listeners.add(listener);
    return () => { this.#listeners.delete(listener); };
  }

  #set(state) {
    this.state = state;
    for (const listener of this.#listeners) listener(state);
  }

  // Browsers only allow audio after the user interacts with the page: call this from a click or key press.
  unlock() {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    this.#context ??= new AudioContext();
    if (this.#context.state === 'suspended') this.#context.resume().catch(() => {});
  }

  async speak(messageId) {
    this.stop();
    const run = this.#run;
    const live = () => run === this.#run;
    const controller = new AbortController();
    this.#controller = controller;
    this.#set('preparing');
    try {
      const plan = await api(`/messages/${messageId}/speech`, { signal: controller.signal });
      if (!plan.parts.length || !live()) return;
      let played = false;
      if (plan.engine === 'gemini' && this.#context?.state === 'running') {
        try {
          played = await this.#stream(`/messages/${messageId}/speech/audio`, plan.sample_rate, live, controller.signal);
        } catch (error) {
          if (!live() || error.name === 'AbortError') return;
          // Gemini speech failed before any audio: the browser's voice reads the reply instead.
        }
      }
      if (!played) {
        for (const part of plan.parts) {
          if (!(await this.#say(part, live))) return;
        }
      }
    } catch {
      // No plan (network, removed message): stay quiet; the reply is still on screen.
    } finally {
      if (live()) {
        this.#controller = null;
        this.#set('idle');
      }
    }
  }

  // Plays the stream as it arrives. Returns whether any audio played; throws if none could.
  async #stream(path, sampleRate, live, signal) {
    const reader = (await fetchStream(path, signal)).body.getReader();
    let pending = new Uint8Array(0);
    let playAt = 0;
    let started = false;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (!live()) return started;
        if (value) {
          const joined = new Uint8Array(pending.length + value.length);
          joined.set(pending);
          joined.set(value, pending.length);
          pending = joined;
        }
        if (done || pending.length >= MIN_PIECE_BYTES) {
          const usable = pending.length - (pending.length % 2);
          if (usable) {
            playAt = this.#schedule(pending.slice(0, usable), sampleRate, playAt);
            pending = pending.slice(usable);
            if (!started) {
              started = true;
              this.#set('speaking');
            }
          }
        }
        if (done) break;
      }
    } catch (error) {
      if (!started) throw error;
      // The stream broke off part-way: finish playing what arrived.
    }
    if (started && live()) await this.#until(playAt);
    return started;
  }

  #schedule(bytes, sampleRate, playAt) {
    const context = this.#context;
    const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.length / 2);
    const buffer = context.createBuffer(1, samples.length, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    // Pieces are queued back to back; after a stall, the next one starts as soon as possible.
    const at = Math.max(playAt, context.currentTime + 0.05);
    source.start(at);
    this.#sources.add(source);
    source.onended = () => this.#sources.delete(source);
    return at + buffer.duration;
  }

  // Waits until the queued audio has played, or until stop().
  #until(time) {
    return new Promise((resolve) => {
      const finish = () => {
        clearTimeout(timer);
        if (this.#settle === finish) this.#settle = null;
        resolve();
      };
      const timer = setTimeout(finish, Math.max(0, time - this.#context.currentTime) * 1000 + 50);
      this.#settle = finish;
    });
  }

  // One utterance per part: Chrome tends to cut long utterances off.
  #say(text, live) {
    const synth = window.speechSynthesis;
    if (!synth || !window.SpeechSynthesisUtterance) return Promise.resolve(false);
    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = this.lang;
      const finish = (ok) => {
        if (this.#settle === finish) this.#settle = null;
        resolve(ok && live());
      };
      this.#settle = finish;
      utterance.onstart = () => { if (live()) this.#set('speaking'); };
      utterance.onend = () => finish(true);
      utterance.onerror = () => finish(false);
      synth.speak(utterance);
    });
  }

  stop() {
    this.#run += 1;
    this.#controller?.abort();
    this.#controller = null;
    for (const source of this.#sources) {
      try { source.stop(); } catch { /* already finished */ }
    }
    this.#sources.clear();
    if (window.speechSynthesis?.speaking || window.speechSynthesis?.pending) window.speechSynthesis.cancel();
    // Cancelled audio and utterances may never report that they ended.
    const settle = this.#settle;
    this.#settle = null;
    settle?.(false);
    if (this.state !== 'idle') this.#set('idle');
  }
}

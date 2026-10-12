// The assistant's face: the agent-robot-avatar Web Component, its saved appearance,
// a director that turns conversation events into expressions, and the settings studio.
import 'agent-robot-avatar';
import { useCallback, useEffect, useRef, useState } from 'react';

const SETTINGS_KEY = 'ceo-workspace-avatar';
// The head uses the app's primary purple (see .primary-button in styles.css).
export const AVATAR_DEFAULTS = Object.freeze({ color: '#6659e5', antenna: true, antennaFlash: true, pointerFollow: true, roundness: 50 });
const ROUNDNESS_STOPS = [0, 50, 100];
// Earlier builds saved every setting, including the old black default; read that as "not chosen".
const FORMER_DEFAULT_COLOR = '#08090b';

// Stored values come from the browser, so anything unexpected falls back to the default.
export function readAvatarSettings() {
  let stored = null;
  try { stored = JSON.parse(window.localStorage.getItem(SETTINGS_KEY)); } catch { /* use defaults */ }
  const value = (key, valid) => (stored && valid(stored[key]) ? stored[key] : AVATAR_DEFAULTS[key]);
  const flag = (item) => typeof item === 'boolean';
  return {
    color: value('color', (item) => /^#[0-9a-f]{6}$/i.test(item) && item.toLowerCase() !== FORMER_DEFAULT_COLOR),
    antenna: value('antenna', flag),
    antennaFlash: value('antennaFlash', flag),
    pointerFollow: value('pointerFollow', flag),
    roundness: value('roundness', (item) => Number.isInteger(item) && item >= 0 && item <= 100),
  };
}

// Only choices that differ from the defaults are stored, so a new default reaches everyone else.
export function saveAvatarSettings(settings) {
  const changed = Object.fromEntries(Object.entries(settings).filter(([key, item]) => item !== AVATAR_DEFAULTS[key]));
  try {
    if (Object.keys(changed).length) window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(changed));
    else window.localStorage.removeItem(SETTINGS_KEY);
  } catch { /* keep for this session only */ }
}

// The component has no option to hide the antenna, so a stylesheet is adopted into its shadow root.
// Adopted sheets survive the component re-rendering its own markup.
let hiddenAntenna = null;
function showAntenna(avatar, visible) {
  const root = avatar.shadowRoot;
  if (!root || !('adoptedStyleSheets' in root)) return;
  if (!hiddenAntenna) {
    hiddenAntenna = new CSSStyleSheet();
    hiddenAntenna.replaceSync('#antennaDot{display:none!important}');
  }
  const others = root.adoptedStyleSheets.filter((sheet) => sheet !== hiddenAntenna);
  root.adoptedStyleSheets = visible ? others : [...others, hiddenAntenna];
}

// `ref` receives the element; keep it stable, or the avatar is detached and reattached on every render.
// Without `size`, the avatar takes its size from the CSS variable --face-size.
export function RobotAvatar({ ref, settings, size, autoSleep = 0, label = 'Assistant' }) {
  const element = useRef(null);
  const attach = useCallback((node) => {
    element.current = node;
    const cleanup = typeof ref === 'function' ? ref(node) : null;
    if (ref && typeof ref === 'object') ref.current = node;
    return () => {
      element.current = null;
      if (typeof cleanup === 'function') cleanup();
      if (ref && typeof ref === 'object') ref.current = null;
    };
  }, [ref]);
  useEffect(() => {
    const avatar = element.current;
    if (!avatar?.setPointerFollow) return;
    avatar.setPointerFollow(settings.pointerFollow);
    avatar.setAntennaFlash(settings.antennaFlash && settings.antenna);
    avatar.setAntennaDrag(settings.antenna);
    avatar.setHeadRoundness(settings.roundness);
    showAntenna(avatar, settings.antenna);
  }, [settings.pointerFollow, settings.antennaFlash, settings.antenna, settings.roundness]);
  return <agent-robot-avatar ref={attach} role="img" aria-label={label} size={size ? String(size) : undefined} color={settings.color} auto-sleep={String(autoSleep)} wake-on="activity" motion="auto"/>;
}

// Keeps a resting state (idle, input or waiting) and plays one-off reactions over it.
// A reaction replaces whatever the avatar is doing, so the resting state is restored afterwards.
// Only one avatar is on screen at a time; the director follows whichever is attached.
export class AvatarDirector {
  element = null;
  base = 'idle';
  applied = 'idle';
  reaction = 0;
  reacting = null;

  attach = (element) => {
    if (!element) return undefined;
    this.element = element;
    this.applied = 'idle';
    this.reacting = null;
    this.applyBase();
    return () => { if (this.element === element) this.element = null; };
  };

  setBase(base) {
    if (base === this.base) return;
    this.base = base;
    if (!this.reacting) this.applyBase();
  }

  applyBase() {
    const avatar = this.element;
    if (!avatar?.startWaiting) return;
    const from = this.applied;
    this.applied = this.base;
    if (this.base === 'waiting') avatar.startWaiting();
    else if (this.base === 'input') avatar.input(true);
    else if (from === 'waiting') avatar.stopWaiting();
    else if (from === 'input') avatar.input(false);
  }

  async react(action, delay = 0) {
    // Several search steps in a row would otherwise restart the same animation each time.
    if (this.reacting === action) return;
    const token = ++this.reaction;
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    const avatar = this.element;
    if (!avatar?.play || token !== this.reaction) return;
    this.reacting = action;
    this.applied = 'idle';
    try {
      // Cap the wait in case an interrupted action never settles.
      await Promise.race([Promise.resolve(avatar.play(action)), new Promise((resolve) => setTimeout(resolve, 6000))]);
    } catch { /* an action interrupted by another */ }
    if (token !== this.reaction) return;
    this.reacting = null;
    this.applyBase();
  }
}

const THANKS = /\b(thanks|thank you|thank u|thx|much appreciated|appreciate (it|that|this)|love (it|this|that)|great job|well done)\b/i;
export const expressesThanks = (text) => THANKS.test(text || '');

// Which reaction suits a progress stage reported by the backend.
export function stageReaction(stage) {
  if (/^(Searching|Reading|Reviewing|Checking documents)/.test(stage || '')) return 'inspect';
  if (stage === 'Calculating a scenario') return 'random';
  return null;
}

// Connection and server problems are system errors; refusals are "blocked"; the rest failed the task.
export function failureReaction(status) {
  if (status === 401 || status === 403 || status === 429) return 'blocked';
  if (!status || status >= 500) return 'error';
  return 'failure';
}

const ACTIONS = [
  ['idle', 'Normal'], ['bored', 'Bored'], ['waiting', 'Waiting'], ['waiting-wrap', 'Waiting · Wrap'], ['input', 'Input'],
  ['send', 'Send/Nod'], ['success', 'Success'], ['failure', 'Failure'], ['warning', 'Warning'], ['inspect', 'Inspect'],
  ['blocked', 'Blocked'], ['error', 'System Error'], ['surprise', 'Surprise'], ['sleep', 'Sleep'], ['wake', 'Wake'],
  ['love', 'Love'], ['random', 'Random'],
];
const STATE_LABELS = {
  idle: 'Idle', bored: 'Bored', waiting: 'Waiting', input: 'Input', send: 'Sending', success: 'Success', happy: 'Success',
  sad: 'Failure', failure: 'Failure', warning: 'Warning', inspect: 'Inspecting', angry: 'Blocked', error: 'System Error',
  surprise: 'Surprise', sleepy: 'Sleepy', sleep: 'Sleeping', wake: 'Awake', love: 'Love', random: 'Random',
  squeeze: 'Squeezed', 'antenna-drag': 'Antenna pulled',
};
const LOOP_PAUSE = { idle: 900, bored: 3350, input: 900, sleep: 800, wake: 500 };
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function Switch({ label, checked, onChange }) {
  return <label className="avatar-switch">
    <span>{label}</span>
    <input type="checkbox" role="switch" checked={checked} onChange={(event) => onChange(event.target.checked)}/>
    <span className="avatar-switch-track" aria-hidden="true"/>
  </label>;
}

// Preview with every expression and the appearance controls; changes apply to the assistant too.
export function AvatarStudio({ settings, onChange }) {
  const avatar = useRef(null);
  const faceState = useRef('idle');
  const loopRef = useRef(false);
  const run = useRef(0);
  const [state, setState] = useState('idle');
  const [active, setActive] = useState(null);
  const [loop, setLoop] = useState(false);
  const [free, setFree] = useState(() => !ROUNDNESS_STOPS.includes(settings.roundness));
  const update = (patch) => onChange({ ...settings, ...patch });

  useEffect(() => {
    const element = avatar.current;
    if (!element) return undefined;
    const onState = (event) => { faceState.current = String(event.detail?.state || 'idle'); setState(faceState.current); };
    element.addEventListener('face-state', onState);
    return () => { element.removeEventListener('face-state', onState); run.current += 1; };
  }, []);

  const setLooping = (value) => {
    loopRef.current = value;
    setLoop(value);
    if (!value) {
      run.current += 1;
      if (faceState.current === 'waiting') avatar.current?.stopWaiting();
    }
  };

  const playOnce = async (element, action, token) => {
    if ((action === 'waiting' || action === 'waiting-wrap') && loopRef.current) {
      await element.startWaiting(action === 'waiting-wrap' ? { variant: 'wrap' } : {});
      return;
    }
    // Replaying a held state needs a fresh start; waking needs something to wake from.
    if ((action === 'input' && faceState.current === 'input') || (action === 'sleep' && faceState.current === 'sleep')) element.reset();
    if (action === 'wake' && faceState.current !== 'sleep') {
      await Promise.resolve(element.play('sleep'));
      if (token !== run.current) return;
      await pause(160);
    }
    if (token === run.current) await Promise.resolve(element.play(action));
  };

  const play = async (action) => {
    const element = avatar.current;
    if (!element?.play) return;
    const token = ++run.current;
    setActive(action);
    do {
      await playOnce(element, action, token);
      if ((action === 'waiting' || action === 'waiting-wrap') && loopRef.current) return;
      if (token !== run.current || !loopRef.current) return;
      await pause(LOOP_PAUSE[action] ?? 140);
    } while (token === run.current && loopRef.current);
  };

  const setRoundness = (value) => update({ roundness: Math.round(Number(value)) });
  const setFreeRoundness = (value) => {
    setFree(value);
    if (!value) setRoundness(ROUNDNESS_STOPS.reduce((best, stop) => (Math.abs(stop - settings.roundness) < Math.abs(best - settings.roundness) ? stop : best)));
  };

  return <div className="avatar-studio">
    <div className="avatar-preview">
      <RobotAvatar ref={avatar} settings={settings} size={104} label="Avatar preview"/>
      <span className="avatar-state" role="status">{state} · {STATE_LABELS[state] || state}</span>
      <p className="avatar-tips"><b>Try:</b> <span>Drag the avatar any direction</span><span>Press and hold the center</span>{settings.antenna && <span>Drag the antenna</span>}</p>
    </div>
    <div className="avatar-actions" role="group" aria-label="Expressions">
      {ACTIONS.map(([action, label]) => <button type="button" key={action} className={active === action ? 'active' : ''} aria-pressed={active === action} onClick={() => play(action)}>{label}</button>)}
    </div>
    <div className="avatar-options">
      <Switch label="Antenna" checked={settings.antenna} onChange={(antenna) => update({ antenna })}/>
      <Switch label="Antenna blink" checked={settings.antennaFlash} onChange={(antennaFlash) => update({ antennaFlash })}/>
      <Switch label="Loop" checked={loop} onChange={setLooping}/>
      <Switch label="Pointer follow" checked={settings.pointerFollow} onChange={(pointerFollow) => update({ pointerFollow })}/>
    </div>
    <div className="avatar-options">
      <div className="avatar-roundness">
        <span>Head roundness</span>
        <span className="roundness-mark square" aria-hidden="true"/>
        <input type="range" min="0" max="100" step={free ? 1 : 50} value={settings.roundness} onChange={(event) => setRoundness(event.target.value)} aria-label="Head roundness" aria-valuetext={`${settings.roundness}%`}/>
        <span className="roundness-mark round" aria-hidden="true"/>
        <Switch label="Free" checked={free} onChange={setFreeRoundness}/>
      </div>
      <label className="avatar-color"><span>Head color</span><input type="color" value={settings.color} onChange={(event) => update({ color: event.target.value })}/></label>
      <button type="button" className="cancel-button" onClick={() => { setFree(false); onChange({ ...AVATAR_DEFAULTS }); }}>Reset</button>
    </div>
  </div>;
}

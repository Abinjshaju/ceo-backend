import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, Download, FileSpreadsheet, FileText, Image as ImageIcon, LogOut, Maximize2, Mic, Minimize2, MoreHorizontal, Paperclip, Plus, Presentation, RotateCcw, Sparkles, Square, Volume2, VolumeX, X } from 'lucide-react';
import { api, checkUpload, newMessageId, postAudio, streamMessage, uploadDocument, SUPPORTED_UPLOADS } from './api.js';
import { ArtifactsView, briefArtifact, toArtifact } from './artifacts.jsx';
import { AVATAR_DEFAULTS, AvatarDirector, RobotAvatar, expressesThanks, failureReaction, stageReaction } from './avatar.jsx';
import { MessageText } from './content.jsx';
import { Speaker } from './speech.js';
import { VoiceListener, microphoneProblem } from './voice.js';

const MAX_FILES = 10;

const INTROS = {
  company: 'Welcome. I’ll help set up your company profile first. Tell me about the company: its name, what it sells, and who its customers are.',
  intake: 'Let’s capture a project. What is it called, what does it do, and which company objective does it support?',
  idle: 'Start a new project, or pick a project to discuss. I answer from each project’s facts, documents, and history.',
};

const PROJECT_SUGGESTIONS = ['Where does this project stand?', 'What are the biggest risks?', 'Refresh the decision brief'];

// Joins dictated text onto the draft with a single space.
function joinText(a, b) {
  return a && b ? `${a.trimEnd()} ${b}` : a || b || '';
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(totalSeconds % 60).padStart(2, '0')}`;
}

function fileKind(file) {
  const name = file.name.toLowerCase();
  if (file.type?.startsWith('image/')) return ImageIcon;
  if (/\.(xls|xlsx|csv)$/.test(name)) return FileSpreadsheet;
  if (/\.(ppt|pptx)$/.test(name)) return Presentation;
  return FileText;
}

export function AttachmentCard({ file, onRemove, onOpen, status }) {
  const Icon = fileKind(file);
  const extension = file.name.includes('.') ? file.name.split('.').pop().toUpperCase() : 'FILE';
  const body = <>
    <span className="file-thumb"><Icon size={16}/></span>
    <div className="file-meta"><b title={file.name}>{file.name}</b><small>{status || `${extension}${file.size ? ` · ${formatBytes(file.size)}` : ''}`}</small></div>
  </>;
  if (onOpen) return <button type="button" className="attachment-card openable" onClick={onOpen} title={`Download ${file.name}`}>{body}<Download size={13} className="open-icon"/></button>;
  return <div className="attachment-card">
    {body}
    {onRemove && <button type="button" className="attachment-remove" onClick={onRemove} aria-label={`Remove ${file.name}`}><X size={11}/></button>}
  </div>;
}

// Reads microphone levels for the waveform. Returns a cleanup function.
function startLevelMeter(onLevels) {
  let stopped = false;
  let stream = null;
  let context = null;
  let frame = 0;
  Promise.resolve()
    .then(() => navigator.mediaDevices.getUserMedia({ audio: true }))
    .then((mic) => {
      if (stopped) { mic.getTracks().forEach((track) => track.stop()); return; }
      stream = mic;
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      context = new AudioCtx();
      const analyser = context.createAnalyser();
      analyser.fftSize = 256;
      context.createMediaStreamSource(mic).connect(analyser);
      const levels = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => { analyser.getByteFrequencyData(levels); onLevels(levels); frame = requestAnimationFrame(tick); };
      tick();
    })
    .catch(() => {});
  return () => {
    stopped = true;
    cancelAnimationFrame(frame);
    stream?.getTracks().forEach((track) => track.stop());
    context?.close();
  };
}

function AmbientScene() {
  return <div className="ambient-scene" aria-hidden="true"><div className="ambient-orb orb-a"/><div className="ambient-orb orb-b"/><div className="ambient-orb orb-c"/><div className="ambient-grid"/><div className="ambient-halo"/></div>;
}

const VOICE_KEY = 'ceo-workspace-voice';

function readVoicePreference() {
  try { return JSON.parse(window.localStorage.getItem(VOICE_KEY)) !== false; } catch { return true; }
}

function VideoPanel({ voiceOn, onToggleVoice, voiceMode, canTalk, onToggleVoiceMode, talking, onStopTalking, activeView, setActiveView, busyLabel, avatar, children }) {
  return (
    <section className={`video-column ${activeView === 'artifacts' ? 'sharing-layout' : ''}`} aria-label={activeView === 'artifacts' ? 'Shared artifact canvas' : 'AI assistant character'}>
      <div className="video-stage">
        <AmbientScene />
        {activeView === 'artifacts' ? children : <>
          <div className="talking-label"><span/> {busyLabel || 'Here with you'}</div>
          <div className="robot-stage">{avatar}</div>
        </>}
      </div>
      <div className="video-controls">
        <div className="control-group">
          <button className="icon-button subtle" aria-label="Show artifacts" title="Show artifacts" onClick={() => setActiveView(activeView === 'artifacts' ? 'assistant' : 'artifacts')}><MoreHorizontal size={21}/></button>
          <button className="icon-button audio-button" aria-label={voiceOn ? 'Turn voice off' : 'Turn voice on'} title={voiceOn ? 'Voice on: answers are read aloud' : 'Voice off'} aria-pressed={voiceOn} onClick={onToggleVoice}>{voiceOn ? <Volume2 size={19}/> : <VolumeX size={19}/>}</button>
          <button type="button" className={`icon-button audio-button voice-mode ${voiceMode ? 'active' : ''}`} aria-pressed={voiceMode}
            aria-label={voiceMode ? 'End voice conversation' : 'Start voice conversation'} title={voiceMode ? 'End voice conversation' : 'Talk with the assistant'}
            disabled={!voiceMode && !canTalk} onClick={onToggleVoiceMode}><Mic size={18}/></button>
          {activeView === 'artifacts' && <button className="icon-button subtle close-presenting" aria-label="Close artifact" onClick={() => setActiveView('assistant')}><X size={19}/></button>}
          {/* Always in place so the controls don't shift; usable only while the assistant is talking. */}
          <button type="button" className="stop-speaking" aria-label="Stop speaking" title="Stop speaking" disabled={!talking} onClick={onStopTalking}><Square size={14} fill="currentColor"/></button>
        </div>
      </div>
    </section>
  );
}

function fromServer(message) {
  return { id: message.id, role: message.role, text: message.content, citations: message.citations || [] };
}

// Loads the conversation the assistant should show for a target.
async function openConversation(target, workspaceId) {
  if (target.kind === 'company' || target.kind === 'intake') {
    const session = await api(`/workspaces/${workspaceId}/onboarding-sessions`, { method: 'POST', body: { kind: target.kind === 'company' ? 'company' : 'project' } });
    return {
      path: `/onboarding-sessions/${session.id}/messages`,
      uploadScope: session.project_id ? `/projects/${session.project_id}` : `/workspaces/${workspaceId}`,
      projectId: session.project_id,
      messages: session.messages.map(fromServer),
      artifacts: [],
    };
  }
  if (target.kind === 'project') {
    const [chat, everything, list, detail] = await Promise.all([
      api(`/projects/${target.projectId}/messages?limit=100`),
      api(`/projects/${target.projectId}/messages?limit=200&include_onboarding=true`),
      api(`/projects/${target.projectId}/artifacts`),
      api(`/projects/${target.projectId}`),
    ]);
    const brief = detail.latest_assessment;
    const chatIds = new Set(chat.messages.map((message) => message.id));
    const intake = everything.messages.filter((message) => !chatIds.has(message.id)).map(fromServer);
    const artifacts = list.artifacts.map((artifact) => ({ ...artifact, id: artifact.artifact_id }));
    if (brief && !artifacts.some((artifact) => artifact.type === 'decision_brief')) artifacts.unshift(briefArtifact(brief, target.projectName));
    return { path: `/projects/${target.projectId}/messages`, uploadScope: `/projects/${target.projectId}`, projectId: target.projectId, earlier: intake, earlierLabel: 'Project intake', messages: chat.messages.map(fromServer), artifacts };
  }
  return { path: null, uploadScope: null, projectId: null, messages: [], artifacts: [] };
}

export function AIChatWidget({ open, setOpen, canClose, onLogout, target, workspaceId, projects = [], language = 'en-US', avatarSettings = AVATAR_DEFAULTS, onChanged, onCompanyComplete, onProjectCreated, onStartProject, onSelectProject }) {
  const [director] = useState(() => new AvatarDirector());
  const [thinking, setThinking] = useState(false);
  const [pageFullscreen, setPageFullscreen] = useState(false);
  const [speaker] = useState(() => new Speaker());
  const [speech, setSpeech] = useState('idle');
  const [voiceOn, setVoiceOn] = useState(readVoicePreference);
  const voiceOnRef = useRef(voiceOn);
  voiceOnRef.current = voiceOn;
  // Set when a finished reply hands over to the next conversation, so its speech carries on.
  const keepSpeaking = useRef(false);
  // Voice conversation: the listener hears a sentence, Gemini transcribes it, and it is sent like typed text.
  const [listener] = useState(() => new VoiceListener());
  const [voiceMode, setVoiceMode] = useState(false);
  const [hearing, setHearing] = useState('off');
  const [transcribing, setTranscribing] = useState(false);
  const voiceModeRef = useRef(false);
  voiceModeRef.current = voiceMode;
  const levelMeter = useRef(null);
  const onSentence = useRef(null);
  const [conversation, setConversation] = useState({ path: null, messages: [], artifacts: [] });
  const [messages, setMessages] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [activeArtifactId, setActiveArtifactId] = useState(null);
  const [activeView, setActiveView] = useState('assistant');
  const [citations, setCitations] = useState(() => new Map());
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [ownAnswer, setOwnAnswer] = useState('');
  const [inputNotice, setInputNotice] = useState('');
  const [voiceElapsed, setVoiceElapsed] = useState(0);
  const [voiceActive, setVoiceActive] = useState(false);
  const dictationBase = useRef('');
  const dictationStart = useRef('');
  const waveRef = useRef(null);
  const chatEnd = useRef(null);
  const input = useRef(null);
  const fileInput = useRef(null);
  const recognition = useRef(null);
  const voiceSession = useRef(false);
  const carryOver = useRef(null);
  const messagesRef = useRef([]);
  messagesRef.current = messages;
  const targetKey = target ? `${target.kind}:${target.projectId || ''}:${target.requestId || ''}` : 'none';

  useEffect(() => {
    // A finished reply handing over to the next conversation keeps talking (and listening).
    if (keepSpeaking.current) keepSpeaking.current = false;
    else {
      speaker.stop();
      listener.stop();
      setVoiceMode(false);
    }
    if (!target || !workspaceId) return undefined;
    let cancelled = false;
    setLoading(true);
    setSuggestions([]);
    setInputNotice('');
    const carried = carryOver.current;
    carryOver.current = null;
    openConversation(target, workspaceId).then((next) => {
      if (cancelled) return;
      const earlier = carried ? carried.messages : next.earlier || [];
      const label = carried ? carried.label : next.earlierLabel;
      const intro = target.kind === 'project' ? `Ask me anything about ${target.projectName}: where it stands, risks, timelines, or what-if scenarios.` : INTROS[target.kind] || INTROS.idle;
      setConversation(next);
      setMessages([
        ...earlier.map((message) => ({ ...message, id: `earlier-${message.id}` })),
        ...(earlier.length ? [{ id: 'divider', divider: label }] : []),
        { id: 'intro', role: 'assistant', text: intro, intro: true },
        ...next.messages,
      ]);
      setArtifacts(next.artifacts);
      setActiveArtifactId(next.artifacts[0]?.id || null);
      setActiveView('assistant');
      setCitations(new Map(next.messages.flatMap((message) => message.citations || []).map((citation) => [citation.ref, citation])));
    }).catch((failure) => {
      if (cancelled) return;
      if (failure.status === 409) onChanged?.();
      setInputNotice(failure.message);
      director.react(failureReaction(failure.status));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [targetKey, workspaceId]);

  // Resting expression: waiting while the assistant works, attentive while you type or dictate.
  const listening = voiceMode && (hearing === 'listening' || hearing === 'hearing');
  const composing = voiceActive || listening || (!busy && Boolean(draft.trim()));
  useEffect(() => { director.setBase(thinking || loading || transcribing ? 'waiting' : composing ? 'input' : 'idle'); }, [director, thinking, loading, transcribing, composing]);

  // Spoken replies: the avatar nods as the voice starts; closing or leaving silences it.
  useEffect(() => {
    let previous = speaker.state;
    const unsubscribe = speaker.subscribe((state) => {
      if (state === 'speaking' && previous !== 'speaking') director.react('send');
      previous = state;
      setSpeech(state);
    });
    return () => { unsubscribe(); speaker.stop(); };
  }, [speaker, director]);
  useEffect(() => { if (!open) speaker.stop(); }, [open, speaker]);

  // Voice conversation turn-taking: the microphone pauses while a sentence is transcribed and while
  // the assistant answers or speaks, so it never hears itself. Stop resumes listening at once.
  useEffect(() => {
    if (voiceMode) listener.setPaused(busy || loading || transcribing || speech !== 'idle');
  }, [voiceMode, busy, loading, transcribing, speech, listener]);
  useEffect(() => {
    const unsubscribe = listener.on((event) => {
      if (event.type === 'state') setHearing(event.state);
      else if (event.type === 'level') levelMeter.current?.style.setProperty('--voice-level', event.level.toFixed(2));
      else if (event.type === 'sentence') onSentence.current?.(event.wav);
      else if (event.type === 'idle') {
        listener.stop();
        setVoiceMode(false);
        setInputNotice('Voice conversation ended after two quiet minutes.');
      }
    });
    return () => { unsubscribe(); listener.stop(); };
  }, [listener]);
  useEffect(() => {
    if (open) return;
    listener.stop();
    setVoiceMode(false);
  }, [open, listener]);
  useEffect(() => { speaker.lang = language; }, [speaker, language]);
  useEffect(() => {
    try { window.localStorage.setItem(VOICE_KEY, JSON.stringify(voiceOn)); } catch { /* this session only */ }
    if (!voiceOn) speaker.stop();
  }, [voiceOn, speaker]);

  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, busy, hearing, transcribing]);
  useEffect(() => { if (open) input.current?.focus({ preventScroll: true }); }, [open]);
  useEffect(() => {
    const exitPageFullscreen = (event) => { if (event.key === 'Escape') setPageFullscreen(false); };
    window.addEventListener('keydown', exitPageFullscreen);
    return () => window.removeEventListener('keydown', exitPageFullscreen);
  }, []);

  const close = () => { if (!canClose) return; setOpen(false); };
  const onboarding = target?.kind === 'company' || target?.kind === 'intake';

  const toggleVoice = () => {
    if (voiceActive) { voiceSession.current = false; recognition.current?.stop(); setVoiceActive(false); return; }
    speaker.stop(); // the microphone shouldn't hear the assistant
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { setInputNotice('Voice input is not available in this browser.'); return; }
    const listener = new SpeechRecognition();
    listener.lang = language;
    listener.continuous = true;
    listener.interimResults = true;
    listener.onresult = (event) => {
      let finalChunk = '';
      let interimChunk = '';
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const spoken = result[0].transcript.trim();
        if (!spoken) continue;
        if (result.isFinal) finalChunk = joinText(finalChunk, spoken);
        else interimChunk = joinText(interimChunk, spoken);
      }
      if (finalChunk) dictationBase.current = joinText(dictationBase.current, finalChunk);
      setDraft(joinText(dictationBase.current, interimChunk));
    };
    listener.onerror = () => { voiceSession.current = false; setInputNotice('Voice input stopped. You can try again or type your message.'); setVoiceActive(false); };
    listener.onend = () => { if (voiceSession.current) { try { listener.start(); } catch { setVoiceActive(false); } } else setVoiceActive(false); };
    recognition.current = listener;
    dictationStart.current = draft;
    dictationBase.current = draft;
    voiceSession.current = true;
    setInputNotice('Listening… speak naturally.');
    try { listener.start(); setVoiceActive(true); } catch { voiceSession.current = false; setInputNotice('Voice input could not start. Check microphone access.'); }
  };
  const stopVoice = () => { if (voiceActive) toggleVoice(); };

  const toggleVoiceMode = async () => {
    if (voiceMode) {
      listener.stop();
      setVoiceMode(false);
      return;
    }
    speaker.unlock(); // within the click, so spoken replies may play
    setVoiceOn(true);
    if (voiceActive) toggleVoice(); // one thing at a time on the microphone
    try {
      await listener.start();
      setVoiceMode(true);
      setInputNotice('');
    } catch (error) {
      listener.stop();
      setInputNotice(microphoneProblem(error));
    }
  };
  const cancelVoice = () => { if (voiceActive) toggleVoice(); setDraft(dictationStart.current); };
  useEffect(() => {
    if (!voiceActive) return undefined;
    setVoiceElapsed(0);
    const timer = window.setInterval(() => setVoiceElapsed((seconds) => seconds + 1), 1000);
    const stopMeter = startLevelMeter((levels) => {
      const bars = waveRef.current?.children || [];
      for (let index = 0; index < bars.length; index += 1) {
        // Mirror the spectrum around the centre: low frequencies (where speech is) peak in the middle.
        const distance = Math.abs(index - (bars.length - 1) / 2) / (bars.length / 2);
        const bin = 1 + Math.floor(distance * 40);
        const level = Math.min(1, levels[bin] / 255 * 1.6);
        bars[index].style.transform = `scaleY(${Math.max(0.14, level)})`;
      }
    });
    return () => { window.clearInterval(timer); stopMeter(); };
  }, [voiceActive]);

  const updateMessage = (id, patch) => setMessages((current) => current.map((message) => (message.id === id ? { ...message, ...(typeof patch === 'function' ? patch(message) : patch) } : message)));

  const addArtifact = (artifact) => {
    setArtifacts((current) => [artifact, ...current.filter((item) => item.id !== artifact.id)]);
    setActiveArtifactId(artifact.id);
    setActiveView('artifacts');
  };

  // `voice`: a transcribed sentence from a voice conversation; it leaves the typed draft and attachments alone.
  const send = async (value = draft, retry = null, { voice = false } = {}) => {
    const text = value.trim();
    const files = retry || voice ? [] : attachments;
    if ((!text && !files.length) || busy || !conversation.path) return;
    stopVoice();
    const clientId = retry?.clientId || newMessageId();
    const content = text || `I've attached ${files.map((item) => item.name).join(', ')}.`;
    const pendingId = `pending-${clientId}`;
    setMessages((current) => [
      ...current.filter((message) => message.id !== retry?.failedId),
      ...(retry ? [] : [{ id: `local-${clientId}`, role: 'user', text: content, voice, files: files.map(({ name, size, type }) => ({ name, size, type })) }]),
      { id: pendingId, role: 'assistant', pending: true, text: '', stage: files.length ? 'Uploading documents' : 'Thinking' },
    ]);
    if (!voice) {
      setDraft('');
      setAttachments([]);
    }
    setSuggestions([]);
    setInputNotice('');
    setBusy(true);
    setThinking(true);
    speaker.stop();
    if (voiceOnRef.current) speaker.unlock(); // within the click or key press, so the reply may play
    director.react(expressesThanks(content) ? 'love' : 'send');
    const documentIds = [...(retry?.documentIds || [])];
    try {
      for (const item of files) documentIds.push((await uploadDocument(conversation.uploadScope, item.file)).id);
    } catch (failure) {
      updateMessage(pendingId, { pending: false, error: `Upload failed: ${failure.message}` });
      director.react('failure');
      setThinking(false);
      setBusy(false);
      return;
    }
    let finished = false;
    let stage = null;
    let saved = false;
    const retryInfo = { clientId, content, documentIds, failedId: pendingId };
    try {
      await streamMessage(conversation.path, { client_message_id: clientId, content, document_ids: documentIds }, (event, data) => {
        if (event === 'progress') {
          updateMessage(pendingId, { stage: data.stage });
          setThinking(true);
          if (data.stage !== stage && stageReaction(data.stage)) director.react(stageReaction(data.stage));
          stage = data.stage;
        } else if (event === 'text_delta') {
          setThinking(false);
          updateMessage(pendingId, (message) => ({ text: message.text + data.text }));
        } else if (event === 'component_ready') {
          addArtifact(toArtifact(data));
          // The artifact view replaces the stage, so react once the small avatar has mounted.
          director.react('surprise', 150);
        } else if (event === 'context_updated') {
          if (data.facts?.length || data.decision_id) saved = true;
        } else if (event === 'completed') {
          finished = true;
          for (const component of data.components || []) addArtifact(toArtifact(component));
          setCitations((current) => new Map([...current, ...(data.citations || []).map((citation) => [citation.ref, citation])]));
          updateMessage(pendingId, { id: data.message_id, pending: false, text: data.text, citations: data.citations || [] });
          setSuggestions(data.suggested_actions || []);
          if (voiceOnRef.current && data.message_id) speaker.speak(data.message_id);
          const intake = data.onboarding?.intake;
          if (target.kind === 'company' && data.onboarding?.status === 'completed') {
            // Keep the finished setup conversation visible above the project intake.
            carryOver.current = { label: 'Your first project', messages: messagesRef.current.filter((message) => !message.intro && !message.divider && !message.pending).concat({ id: data.message_id, role: 'assistant', text: data.text, citations: [] }) };
            keepSpeaking.current = true;
            director.react('success');
            onCompanyComplete?.();
          }
          else if (target.kind === 'intake' && intake?.project_status === 'active') {
            keepSpeaking.current = true;
            director.react('success');
            onProjectCreated?.(intake.project_id);
          } else {
            if (saved) director.react('success');
            onChanged?.();
          }
        } else if (event === 'failed') {
          finished = true;
          director.react(failureReaction(data.status));
          updateMessage(pendingId, { pending: false, error: data.error, retry: data.status && data.status < 500 ? null : retryInfo });
        }
      });
      if (!finished) {
        director.react('error');
        updateMessage(pendingId, { pending: false, error: 'The connection closed before the answer finished.', retry: retryInfo });
      }
    } catch (failure) {
      director.react(failureReaction(failure.status));
      updateMessage(pendingId, { pending: false, error: failure.message, retry: failure.status && failure.status < 500 ? null : retryInfo });
    } finally {
      setThinking(false);
      setBusy(false);
    }
  };

  // Runs with this render's state; sending goes through the latest send() via the ref.
  const latestSend = useRef(send);
  latestSend.current = send;
  onSentence.current = async (wav) => {
    setTranscribing(true);
    try {
      const { text } = await postAudio(`/workspaces/${workspaceId}/transcriptions?language=${encodeURIComponent(language)}`, wav);
      if (!voiceModeRef.current) return;
      if (text) latestSend.current(text, null, { voice: true });
      else setInputNotice('Didn’t catch that. Please say it again.');
    } catch (failure) {
      if (voiceModeRef.current) {
        setInputNotice(failure.status === 503 ? 'Transcription is unavailable right now. You can still type.' : `Couldn’t transcribe that: ${failure.message}`);
      }
    } finally {
      setTranscribing(false);
    }
  };

  const addFiles = (list) => {
    const incoming = Array.from(list || []);
    const room = Math.max(0, MAX_FILES - attachments.length);
    const problems = incoming.map(checkUpload).filter(Boolean);
    const accepted = incoming.filter((file) => !checkUpload(file)).slice(0, room).map((file) => ({ id: `${Date.now()}-${Math.random()}`, file, name: file.name, size: file.size, type: file.type }));
    if (accepted.length) setAttachments((current) => [...current, ...accepted]);
    if (problems.length || incoming.length > accepted.length) director.react('warning');
    if (problems.length) setInputNotice(problems.join(' '));
    else if (incoming.length > accepted.length) setInputNotice(`You can attach up to ${MAX_FILES} files at a time.`);
    else if (accepted.length) setInputNotice(`${accepted.length} file${accepted.length > 1 ? 's' : ''} ready to send.`);
  };

  if (!open) return <button className="launcher" onClick={() => setOpen(true)} aria-label="Open assistant"><Sparkles size={23}/></button>;

  const pending = messages.find((message) => message.pending);
  const voiceLabel = !voiceMode ? null : transcribing ? 'Transcribing…' : hearing === 'hearing' ? 'Hearing you…' : hearing === 'listening' ? 'Listening…' : null;
  // Shown on the stage, or in the chat header while an artifact covers the stage.
  const status = pending?.stage || (speech === 'speaking' ? 'Speaking…' : null) || voiceLabel;
  const title =target?.kind === 'company' ? 'Company setup' : target?.kind === 'intake' ? 'New project' : target?.kind === 'project' ? target.projectName : 'Assistant';
  const showProjectSuggestions = target?.kind === 'project' && !busy && !loading && !suggestions.length && !messages.some((message) => message.role === 'user' && !String(message.id).startsWith('earlier-'));

  return (
    <div className="overlay" role="presentation" onMouseDown={(event) => { if (canClose && event.target === event.currentTarget) close(); }}>
      <main className={`assistant-modal ${pageFullscreen ? 'page-fullscreen' : ''}`} role="dialog" aria-modal="true" aria-label="AI assistant">
        <header className="modal-header">
          <span className="assistant-title"><span className="title-dot"/> AI assistant · {title}</span>
          <div className="header-actions">
            <button className="icon-button header-icon" aria-label={pageFullscreen ? 'Exit expanded view' : 'Expand to fill page'} title={pageFullscreen ? 'Exit expanded view' : 'Expand to fill page'} onClick={() => setPageFullscreen((current) => !current)}>{pageFullscreen ? <Minimize2 size={15}/> : <Maximize2 size={15}/>}</button>
            {canClose && <button className="icon-button header-icon" aria-label="Close assistant" onClick={close}><X size={16}/></button>}
            {/* Setup can't be dismissed, and the sidebar's log out is behind it. */}
            {!canClose && onLogout && <button className="icon-button header-icon" aria-label="Log out" title="Log out" onClick={onLogout}><LogOut size={15}/></button>}
          </div>
        </header>
        <div className={`modal-content ${activeView === 'artifacts' ? 'presentation-mode' : ''}`}>
          <VideoPanel voiceOn={voiceOn} onToggleVoice={() => { if (!voiceOn) speaker.unlock(); setVoiceOn(!voiceOn); }} talking={speech !== 'idle'} onStopTalking={() => speaker.stop()}
            voiceMode={voiceMode} canTalk={Boolean(conversation.path)} onToggleVoiceMode={toggleVoiceMode}
            activeView={activeView} setActiveView={setActiveView} busyLabel={status}
            avatar={<RobotAvatar ref={director.attach} settings={avatarSettings} autoSleep={60000}/>}>
            <ArtifactsView artifacts={artifacts} activeId={activeArtifactId} setActiveId={setActiveArtifactId} projectId={conversation.projectId} onProjectChanged={onChanged} citations={citations}/>
          </VideoPanel>
          <section className={`chat-column ${activeView === 'artifacts' ? 'presentation-chat' : ''}`} aria-label="Chat with the assistant">
            {activeView === 'artifacts' && <div className="presentation-chat-head"><span className="presentation-agent-icon"><RobotAvatar ref={director.attach} settings={avatarSettings} size={26}/></span><div><b>Assistant</b><small className={status ? 'presentation-status' : undefined}>{status || title}</small></div><span className="participant-count">{artifacts.length}</span></div>}
            <div className="conversation" aria-live="polite" aria-busy={busy || loading}>
              {loading && <div className="message assistant-message typing" aria-label="Loading conversation"><i/><i/><i/></div>}
              {messages.map((message) => message.divider ? <div key={message.id} className="history-divider"><span>{message.divider}</span></div> : <div key={message.id} data-pending={message.pending ? 'true' : undefined} className={`message ${message.role === 'user' ? 'user-message' : 'assistant-message'} ${message.error ? 'error-message' : ''}`}>
                {message.files?.length > 0 && <div className="message-files">{message.files.map((file, index) => <AttachmentCard key={index} file={file}/>)}</div>}
                {message.role === 'user' ? <>{message.voice && <span className="message-voice-icon" title="Spoken" aria-label="Spoken:"><Mic size={12} aria-hidden="true"/></span>}{message.text}</> : message.error ? <>
                  <span>{message.error}</span>
                  {message.retry && <button type="button" className="retry-button" onClick={() => send(message.retry.content, message.retry)} disabled={busy}><RotateCcw size={12}/> Retry</button>}
                </> : message.pending && !message.text ? <span className="stage-label"><i/><i/><i/> {message.stage}…</span> : <MessageText text={message.text} citations={message.pending ? [] : message.citations}/>}
              </div>)}
              {voiceMode && (hearing === 'hearing' || transcribing) && <div className="message user-message voice-live" role="status" aria-label={transcribing ? 'Transcribing what you said' : 'Hearing you'}>
                <Mic size={13} aria-hidden="true"/>
                {transcribing ? <span>Transcribing…</span> : <span className="voice-level" ref={levelMeter} aria-hidden="true"><i/><i/><i/><i/><i/></span>}
              </div>}
              <div ref={chatEnd}/>
            </div>
            <div className="chat-bottom" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }}>
              {target?.kind === 'idle' && !busy && <div className="choice-card project-picker" aria-live="polite">
                <p className="choice-card-title">What would you like to work on?</p>
                <div className="choice-options">
                  {projects.filter((project) => project.status === 'active').map((project) => <button type="button" key={project.id} className="choice-option" onClick={() => onSelectProject?.(project.id)}><span>{project.name.slice(0, 1).toUpperCase()}</span><div className="picker-text"><b>{project.name}</b><small>{project.score != null ? `Score ${project.score}` : 'Not scored yet'} · {project.feasibility || 'Not assessed'}</small></div></button>)}
                  <button type="button" className="choice-option new" onClick={() => onStartProject?.()}><span><Plus size={12}/></span><div className="picker-text"><b>New project</b><small>Capture a project with the assistant</small></div></button>
                </div>
              </div>}
              {showProjectSuggestions && <div className="suggestions" aria-label="Suggested questions">{PROJECT_SUGGESTIONS.map((suggestion) => <button key={suggestion} className="suggestion" onClick={() => send(suggestion)}>{suggestion}</button>)}</div>}
              {suggestions.length > 0 && !busy && (onboarding ? <div className="choice-card" aria-live="polite">
                <p className="choice-card-title">Choose an answer or write your own</p>
                <div className="choice-options">{suggestions.map((option, index) => <button type="button" key={option.label} className="choice-option" onClick={() => send(option.label)}><span>{index + 1}</span>{option.label}</button>)}</div>
                <form className="choice-own" onSubmit={(event) => { event.preventDefault(); const value = ownAnswer.trim(); if (!value) return; setOwnAnswer(''); send(value); }}>
                  <input value={ownAnswer} onChange={(event) => setOwnAnswer(event.target.value)} placeholder="Write your own answer…" aria-label="Write your own answer"/>
                  <button type="submit" disabled={!ownAnswer.trim()}>Send</button>
                </form>
              </div> : <div className="suggestions">{suggestions.map((option) => <button key={option.label} className="suggestion" onClick={() => send(option.label)}>{option.label}</button>)}</div>)}
              {attachments.length > 0 && <div className="attachment-tray" aria-label="Attached files">{attachments.map((item) => <AttachmentCard key={item.id} file={item} onRemove={() => setAttachments((current) => current.filter((file) => file.id !== item.id))}/>)}</div>}
              {inputNotice && !voiceActive && <div className="input-notice" role="status">{inputNotice}</div>}
              <form className={`composer ${voiceActive ? 'recording' : ''}`} onSubmit={(event) => { event.preventDefault(); send(); }}>
                <input ref={input} value={draft} readOnly={voiceActive} onChange={(event) => setDraft(event.target.value)} placeholder={voiceActive ? 'Listening…' : conversation.path ? 'Type your message...' : 'Choose a project or start a new one'} aria-label="Type your message" disabled={!conversation.path && !voiceActive}/>
                <input ref={fileInput} className="hidden-file-input" type="file" accept={SUPPORTED_UPLOADS.join(',')} multiple onChange={(event) => { addFiles(event.target.files); event.target.value = ''; }}/>
                {voiceActive ? <div className="dictation-bar">
                  <button type="button" className="dictation-cancel" onClick={cancelVoice} aria-label="Cancel dictation"><X size={16}/></button>
                  <div className="dictation-meter"><span className="voice-dot"/><span className="voice-time">{formatDuration(voiceElapsed)}</span></div>
                  <div className="voice-wave" ref={waveRef} aria-hidden="true">{Array.from({ length: 36 }, (_, index) => <span key={index}/>)}</div>
                  <button type="button" className="dictation-confirm" onClick={stopVoice} aria-label="Finish dictation"><Check size={16}/></button>
                </div> : <div className="composer-tools">
                  <button type="button" className="composer-tool" onClick={() => fileInput.current?.click()} aria-label="Attach a file" disabled={!conversation.uploadScope}><Paperclip size={17}/></button>
                  <button type="button" className="composer-tool" onClick={toggleVoice} aria-label="Use voice input" disabled={voiceMode} title={voiceMode ? 'Unavailable during a voice conversation' : 'Dictate a message'}><Mic size={17}/></button>
                  <span className="composer-spacer"/>
                  <button type="submit" className="send-button" disabled={(!draft.trim() && !attachments.length) || busy || !conversation.path} aria-label="Send message"><ArrowUp size={18}/></button>
                </div>}
              </form>
              <p className="disclaimer">AI-generated responses may be inaccurate. Check important figures against their sources.</p>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

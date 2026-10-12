// Client for the workspace API. The token comes from /api/auth/login or /api/auth/signup.

const TOKEN_KEY = 'ceo-workspace-token';
const API = '/api';

export const SUPPORTED_UPLOADS = ['.pdf', '.docx', '.xlsx', '.csv', '.txt', '.md'];
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export class ApiError extends Error {
  constructor(status, message, details = {}) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

// Holds the token only when storage refused it, so the session still lasts for this tab.
let memoryToken = null;
let memoryFallback = false;

export function getToken() {
  if (memoryFallback) return memoryToken;
  try { return window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function setToken(token) {
  memoryToken = null;
  memoryFallback = false;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage can be blocked or full, for example in some private windows.
    memoryToken = token || null;
    memoryFallback = true;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(handler) { onUnauthorized = handler; }

function headers(json) {
  const result = {};
  const token = getToken();
  if (token) result.Authorization = `Bearer ${token}`;
  if (json) result['Content-Type'] = 'application/json';
  return result;
}

async function failure(response) {
  const body = await response.json().catch(() => null);
  const error = body?.error || {};
  if (response.status === 401 && getToken()) onUnauthorized();
  return new ApiError(response.status, error.message || `Request failed (${response.status})`, error);
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: headers(body !== undefined),
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return null;
  return response.json();
}

// Sends a recording as the raw request body and returns the JSON reply.
export async function postAudio(path, blob, signal) {
  const response = await fetch(`${API}${path}`, {
    method: 'POST', headers: { ...headers(false), 'Content-Type': blob.type || 'audio/wav' }, body: blob, signal,
  });
  if (!response.ok) throw await failure(response);
  return response.json();
}

// A streamed response (such as spoken audio), for reading while it arrives.
export async function fetchStream(path, signal) {
  const response = await fetch(`${API}${path}`, { headers: headers(false), signal });
  if (!response.ok) throw await failure(response);
  return response;
}

// Posts a chat message and reports each Server-Sent Event as it arrives.
export async function streamMessage(path, body, onEvent, signal) {
  const response = await fetch(`${API}${path}?stream=true`, {
    method: 'POST', headers: headers(true), body: JSON.stringify(body), signal,
  });
  if (!response.ok) throw await failure(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = 'message';
      let data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data += line.slice(6);
      }
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

export function checkUpload(file) {
  const name = file.name.toLowerCase();
  if (!SUPPORTED_UPLOADS.some((suffix) => name.endsWith(suffix))) return `${file.name}: use PDF, Word, Excel, CSV, text or Markdown files.`;
  if (file.size > MAX_UPLOAD_BYTES) return `${file.name} is larger than 25 MB.`;
  if (!file.size) return `${file.name} is empty.`;
  return '';
}

// Signed upload: ask for a URL, send the file there, then confirm so processing starts.
export async function uploadDocument(scopePath, file, documentId = null) {
  const target = await api(`${scopePath}/documents/upload-url`, {
    method: 'POST', body: { filename: file.name, size_bytes: file.size, document_id: documentId },
  });
  const put = await fetch(target.upload.url, { method: target.upload.method, headers: target.upload.headers, body: file });
  if (!put.ok) throw await failure(put);
  return api(`${scopePath}/documents/${target.document_id}/complete`, { method: 'POST', body: { version_id: target.version_id } });
}

export async function downloadDocument(scopePath, documentId) {
  const { url } = await api(`${scopePath}/documents/${documentId}/download-url`);
  const link = document.createElement('a');
  link.href = url;
  link.rel = 'noopener';
  link.click();
}

export function newMessageId() {
  return window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

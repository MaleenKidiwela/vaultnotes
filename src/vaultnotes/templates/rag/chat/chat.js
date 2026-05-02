// Frontend chat for vaultnotes RAG. Loads CONFIG.workerUrl from chat/config.json
// (written by `vaultnotes rag set-worker-url`), then runs hybrid retrieval locally
// over the prebuilt index in /public.

import MiniSearch from 'https://cdn.jsdelivr.net/npm/minisearch@7.1.0/+esm';

const PATHS = {
  config: 'config.json',
  index: '/public/search-index.json',
  embeddings: '/public/embeddings.bin',
  chunks: '/public/chunks.json',
};

const CONFIG = {
  workerUrl: '',
  embedDim: 768,
  bm25K: 14,
  semanticK: 14,
  lexicalK: 10,
  fuseK: 6,
  neighborWindow: 1,
  maxContextChars: 18000,
};

const $ = (id) => document.getElementById(id);

let password = null;
let mini = null;
let chunks = null;
let embeddings = null;
let assetsReady = false;

function setStatus(text, isError = false) {
  const el = $('status');
  el.textContent = text;
  el.classList.toggle('error', !!isError);
}

async function loadFrontendConfig() {
  try {
    const r = await fetch(PATHS.config, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`config.json ${r.status}`);
    const j = await r.json();
    if (j.workerUrl) CONFIG.workerUrl = j.workerUrl;
    if (j.embedDim) CONFIG.embedDim = j.embedDim;
  } catch (e) {
    $('pwError').textContent = `Could not load chat config: ${e.message}`;
  }
}

function workerUrlMissing() {
  return !CONFIG.workerUrl || /YOUR-ACCOUNT|TODO/.test(CONFIG.workerUrl);
}

await loadFrontendConfig();

$('pwForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const pw = $('pwInput').value;
  $('pwError').textContent = '';
  $('pwBtn').disabled = true;

  if (workerUrlMissing()) {
    $('pwError').textContent = 'Worker URL not configured. Run `vaultnotes rag set-worker-url <url>` after deploying the Worker.';
    $('pwBtn').disabled = false;
    return;
  }

  try {
    const r = await fetch(`${CONFIG.workerUrl}/embed`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: pw, text: 'ping' }),
    });
    if (r.status === 401) {
      $('pwError').textContent = 'Wrong password.';
      $('pwInput').value = '';
      $('pwBtn').disabled = false;
      return;
    }
    if (!r.ok) {
      const detail = await r.text().catch(() => '');
      $('pwError').textContent = `Worker error (${r.status}). ${detail.slice(0, 200)}`;
      $('pwBtn').disabled = false;
      return;
    }
    password = pw;
    $('pwScreen').hidden = true;
    $('chatScreen').hidden = false;
    await loadAssets();
  } catch (err) {
    $('pwError').textContent = `Network error: ${err.message}`;
    $('pwBtn').disabled = false;
  }
});

async function loadAssets() {
  setStatus('Loading index…');
  try {
    const [idxText, chunkData, embBuf] = await Promise.all([
      fetchText(PATHS.index, 'search-index.json'),
      fetchJson(PATHS.chunks, 'chunks.json'),
      fetchBuffer(PATHS.embeddings, 'embeddings.bin'),
    ]);
    mini = MiniSearch.loadJSON(idxText, {
      fields: ['searchText', 'noteTitle', 'sectionTitle', 'project', 'date'],
      storeFields: [
        'noteTitle', 'noteUrl', 'chunkIndex', 'noteId', 'sectionTitle',
        'sectionId', 'sectionChunkIndex', 'sectionChunkCount', 'project', 'date',
      ],
      idField: 'id',
    });
    chunks = chunkData;
    embeddings = new Float32Array(embBuf);
    if (embeddings.length !== chunks.length * CONFIG.embedDim) {
      throw new Error(`embedding/chunk mismatch (${embeddings.length} vs ${chunks.length * CONFIG.embedDim})`);
    }
    assetsReady = true;
    setStatus(`Ready. ${chunks.length} chunks across ${countNotes()} notes.`);
  } catch (e) {
    setStatus(`Index not available: ${e.message}. The chat will work after the GitHub Action has built /public.`, true);
  }
}

function countNotes() {
  const s = new Set();
  for (const c of chunks) s.add(c.noteId);
  return s.size;
}

async function fetchText(url, label) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${label} ${r.status}`);
  return r.text();
}
async function fetchJson(url, label) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${label} ${r.status}`);
  return r.json();
}
async function fetchBuffer(url, label) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${label} ${r.status}`);
  return r.arrayBuffer();
}

$('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = $('qInput').value.trim();
  if (!q) return;
  $('qInput').value = '';
  appendUser(q);
  if (!assetsReady) {
    appendAssistant().textEl.textContent = 'Index is not loaded yet.';
    return;
  }
  $('qBtn').disabled = true;
  try {
    await ask(q);
  } finally {
    $('qBtn').disabled = false;
    $('qInput').focus();
  }
});

function appendUser(text) {
  const div = document.createElement('div');
  div.className = 'msg msg-user';
  div.textContent = text;
  $('messages').appendChild(div);
  scrollDown();
}

function appendAssistant() {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant';
  const avatar = document.createElement('img');
  avatar.src = 'fox.svg';
  avatar.className = 'avatar';
  avatar.alt = '';
  const body = document.createElement('div');
  body.className = 'body';
  const textEl = document.createElement('div');
  textEl.className = 'text';
  body.appendChild(textEl);
  const cites = document.createElement('div');
  cites.className = 'citations';
  body.appendChild(cites);
  wrap.appendChild(avatar);
  wrap.appendChild(body);
  $('messages').appendChild(wrap);
  scrollDown();
  return { textEl, cites };
}

function scrollDown() {
  const m = $('messages');
  m.scrollTop = m.scrollHeight;
}

async function ask(query) {
  const { textEl, cites } = appendAssistant();
  textEl.textContent = '…';

  const direct = directResponse(query);
  if (direct) {
    textEl.textContent = direct;
    return;
  }

  let qEmb;
  try {
    const r = await fetch(`${CONFIG.workerUrl}/embed`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password, text: query }),
    });
    if (r.status === 401) {
      password = null;
      $('chatScreen').hidden = true;
      $('pwScreen').hidden = false;
      $('pwBtn').disabled = false;
      $('pwError').textContent = 'Session expired. Re-enter the password.';
      return;
    }
    if (!r.ok) throw new Error(`embed ${r.status}`);
    qEmb = (await r.json()).embedding;
  } catch (e) {
    textEl.textContent = `Embedding error: ${e.message}`;
    return;
  }
  const qVec = l2norm(new Float32Array(qEmb));

  const bm25 = mini.search(query, { fuzzy: 0.2, prefix: true })
    .slice(0, CONFIG.bm25K).map((r) => ({ id: r.id }));
  const lexical = lexicalSearch(query, CONFIG.lexicalK);
  const semantic = cosineTopK(qVec, CONFIG.semanticK);
  const fused = rrf(bm25, semantic, lexical, CONFIG.fuseK);
  const expanded = expandWithNeighbors(fused, CONFIG.neighborWindow);
  const packed = packContext(expanded, CONFIG.maxContextChars);

  if (packed.length === 0) {
    textEl.textContent = 'No relevant notes found for that query.';
    return;
  }

  const context = packed.map((c) => {
    const section = c.sectionTitle ? ` / ${c.sectionTitle}` : '';
    return `### ${c.noteTitle}${section}\n${c.text}`;
  }).join('\n\n---\n\n');

  cites.innerHTML = '';
  const seen = new Set();
  for (const c of packed) {
    if (seen.has(c.noteId)) continue;
    seen.add(c.noteId);
    const a = document.createElement('a');
    a.className = 'chip';
    a.href = c.noteUrl;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = c.sectionTitle ? `${c.noteTitle} · ${c.sectionTitle}` : c.noteTitle;
    cites.appendChild(a);
  }

  textEl.textContent = '';
  let gotText = false;
  let lastFinish = null;
  let upstreamErr = null;
  try {
    const r = await fetch(`${CONFIG.workerUrl}/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password, query, context }),
    });
    if (!r.ok || !r.body) {
      const t = await r.text().catch(() => '');
      textEl.textContent = `Chat error (${r.status}): ${t.slice(0, 300)}`;
      return;
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    const handleLine = (line) => {
      const m = line.match(/^data:\s?(.*)$/);
      if (!m) return;
      const payload = m[1].trim();
      if (!payload || payload === '[DONE]') return;
      try {
        const j = JSON.parse(payload);
        if (j.error) { upstreamErr = j.error; return; }
        const cand = j?.candidates?.[0];
        const parts = cand?.content?.parts || [];
        for (const p of parts) {
          if (typeof p.text === 'string' && p.text) {
            textEl.textContent += p.text;
            gotText = true;
            scrollDown();
          }
        }
        if (cand?.finishReason) lastFinish = cand.finishReason;
        if (j?.promptFeedback?.blockReason) lastFinish = `blocked: ${j.promptFeedback.blockReason}`;
      } catch {}
    };
    const flush = () => {
      buf = buf.replace(/\r\n/g, '\n');
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const event = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of event.split('\n')) handleLine(line);
      }
    };
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      flush();
    }
    if (buf.trim()) {
      for (const line of buf.split('\n')) handleLine(line);
    }
    if (!gotText) {
      const detail = upstreamErr ? JSON.stringify(upstreamErr) : (lastFinish || 'no text returned');
      textEl.textContent = `(empty response — ${detail})`;
    }
  } catch (e) {
    textEl.textContent += `\n[stream error: ${e.message}]`;
  }
}

function directResponse(query) {
  const q = normalizeLexical(query);
  if (/^(hi|hello|hey|yo|sup|howdy)$/.test(q)) {
    return 'Hi. Ask me a question about your synced notes and I will answer from the indexed content.';
  }
  if (/^(thanks|thank you|thx)$/.test(q)) {
    return 'You are welcome.';
  }
  if (/^(help|what can you do|what do you do)$/.test(q)) {
    return 'I can answer questions about your synced notes, cite the notes I used, and help you find relevant sections. Try asking about a project, date, note title, or specific term.';
  }
  return null;
}

function l2norm(v) {
  let s = 0;
  for (let i = 0; i < v.length; i++) s += v[i] * v[i];
  s = Math.sqrt(s) || 1;
  const out = new Float32Array(v.length);
  for (let i = 0; i < v.length; i++) out[i] = v[i] / s;
  return out;
}

function cosineTopK(qVec, k) {
  const D = CONFIG.embedDim;
  const N = chunks.length;
  const scored = new Array(N);
  for (let i = 0; i < N; i++) {
    let s = 0;
    const off = i * D;
    for (let d = 0; d < D; d++) s += qVec[d] * embeddings[off + d];
    scored[i] = { id: i, score: s };
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, k);
}

function lexicalSearch(query, k) {
  const q = normalizeLexical(query);
  if (!q) return [];
  const scored = [];
  for (const c of chunks) {
    const haystacks = [
      [c.noteTitle, 8],
      [c.noteId, 7],
      [c.sectionTitle, 5],
      [c.project, 4],
      [c.date, 4],
      [c.text, 1],
    ];
    let score = 0;
    for (const [value, weight] of haystacks) {
      const text = normalizeLexical(value || '');
      if (!text) continue;
      if (text === q) score += weight * 8;
      else if (text.startsWith(q)) score += weight * 5;
      else if (text.includes(q)) score += weight * 2;
    }
    if (score > 0) scored.push({ id: c.id, score });
  }
  return scored
    .sort((a, b) => b.score - a.score)
    .slice(0, k);
}

function normalizeLexical(value) {
  return String(value)
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[_\-./]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function rrf(bm25, semantic, lexical, k, c = 60) {
  const map = new Map();
  bm25.forEach((r, rank) => map.set(r.id, (map.get(r.id) || 0) + 1 / (c + rank)));
  semantic.forEach((r, rank) => map.set(r.id, (map.get(r.id) || 0) + 1 / (c + rank)));
  lexical.forEach((r, rank) => map.set(r.id, (map.get(r.id) || 0) + 1.5 / (c + rank)));
  return [...map.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, k)
    .map(([id]) => chunks[id])
    .filter(Boolean);
}

function expandWithNeighbors(seedChunks, windowSize = 1) {
  const wanted = new Map();
  const bySection = new Map();
  for (const c of chunks) {
    const key = c.sectionId || c.noteId;
    const arr = bySection.get(key) || [];
    arr.push(c);
    bySection.set(key, arr);
  }
  for (const arr of bySection.values()) {
    arr.sort((a, b) => (a.sectionChunkIndex ?? a.chunkIndex ?? 0) - (b.sectionChunkIndex ?? b.chunkIndex ?? 0));
  }

  seedChunks.forEach((chunk, seedRank) => {
    const key = chunk.sectionId || chunk.noteId;
    const arr = bySection.get(key) || [chunk];
    const idx = arr.findIndex((c) => c.id === chunk.id);
    const center = idx === -1 ? 0 : idx;
    for (let offset = -windowSize; offset <= windowSize; offset++) {
      const neighbor = arr[center + offset];
      if (!neighbor) continue;
      const distance = Math.abs(offset);
      const existing = wanted.get(neighbor.id);
      const rank = seedRank + distance * 0.25;
      if (!existing || rank < existing.rank) {
        wanted.set(neighbor.id, { chunk: neighbor, rank });
      }
    }
  });

  return [...wanted.values()]
    .sort((a, b) => a.rank - b.rank)
    .map((x) => x.chunk);
}

function packContext(candidateChunks, maxChars) {
  const packed = [];
  const seen = new Set();
  let chars = 0;
  for (const c of candidateChunks) {
    if (!c || seen.has(c.id)) continue;
    const section = c.sectionTitle ? ` / ${c.sectionTitle}` : '';
    const blockLen = `### ${c.noteTitle}${section}\n${c.text}`.length + 12;
    if (packed.length && chars + blockLen > maxChars) continue;
    packed.push(c);
    seen.add(c.id);
    chars += blockLen;
  }
  return packed;
}

// Cloudflare Worker: password-gated proxy to Gemini for vaultnotes RAG chat.
// Endpoints:
//   POST /embed  { password, text }            -> { embedding: number[] }
//   POST /chat   { password, query, context }  -> SSE stream

const EMBED_MODEL = 'gemini-embedding-001';
const EMBED_DIM = 768;
const CHAT_MODEL = 'gemini-2.5-flash';
const MAX_QUERY_LEN = 2000;
const MAX_CONTEXT_LEN = 20000;

const SYSTEM_PROMPT = `You are a research assistant answering questions about a personal collection of notes.
Use ONLY the notes provided in the context to answer.
If the answer isn't in the context, say so clearly and suggest what
related topics might be worth searching for instead.
Cite specific notes by title when you reference them.
Do not use em dashes in your writing.`;

function corsHeaders(env, origin) {
  const allowed = env.ALLOWED_ORIGIN || '*';
  const allow = origin && (allowed === '*' || origin === allowed) ? origin : allowed;
  return {
    'access-control-allow-origin': allow,
    'access-control-allow-methods': 'POST, OPTIONS',
    'access-control-allow-headers': 'content-type',
    'access-control-max-age': '86400',
    vary: 'origin',
  };
}

function timingSafeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function json(data, status, cors) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...cors, 'content-type': 'application/json' },
  });
}

async function readJson(req) {
  try {
    return await req.json();
  } catch {
    return null;
  }
}

async function fetchWithRetry(url, init, { tries = 4, baseMs = 800 } = {}) {
  let last;
  for (let i = 0; i < tries; i++) {
    const res = await fetch(url, init);
    if (res.ok) return res;
    if (res.status !== 429 && res.status !== 503 && res.status < 500) return res;
    last = res;
    if (i === tries - 1) break;
    let wait = baseMs * Math.pow(2, i);
    const ra = res.headers.get('retry-after');
    if (ra && !Number.isNaN(parseInt(ra, 10))) wait = Math.max(wait, parseInt(ra, 10) * 1000);
    try { await res.arrayBuffer(); } catch {}
    await new Promise((r) => setTimeout(r, wait));
  }
  return last;
}

export default {
  async fetch(req, env) {
    const origin = req.headers.get('origin') || '';
    const cors = corsHeaders(env, origin);

    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });

    const url = new URL(req.url);

    if (req.method === 'GET' && url.pathname === '/') {
      return new Response('vaultnotes-rag worker ok', { status: 200, headers: cors });
    }

    if (req.method !== 'POST') return json({ error: 'method not allowed' }, 405, cors);

    const body = await readJson(req);
    if (!body) return json({ error: 'invalid json' }, 400, cors);

    if (!env.CHAT_PASSWORD || !timingSafeEqual(String(body.password || ''), env.CHAT_PASSWORD)) {
      return json({ error: 'unauthorized' }, 401, cors);
    }
    if (!env.GEMINI_API_KEY) {
      return json({ error: 'server misconfigured: missing GEMINI_API_KEY' }, 500, cors);
    }

    if (url.pathname === '/embed') {
      const text = String(body.text || '').trim();
      if (!text) return json({ error: 'text required' }, 400, cors);
      if (text.length > MAX_QUERY_LEN) return json({ error: 'text too long' }, 400, cors);

      const upstream = await fetchWithRetry(
        `https://generativelanguage.googleapis.com/v1beta/models/${EMBED_MODEL}:embedContent?key=${env.GEMINI_API_KEY}`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            model: `models/${EMBED_MODEL}`,
            content: { parts: [{ text }] },
            outputDimensionality: EMBED_DIM,
            taskType: 'RETRIEVAL_QUERY',
          }),
        },
      );
      if (!upstream.ok) {
        const detail = await upstream.text().catch(() => '');
        return json({ error: 'embed upstream failed', status: upstream.status, detail: detail.slice(0, 500) }, 502, cors);
      }
      const j = await upstream.json();
      return json({ embedding: j.embedding?.values || [] }, 200, cors);
    }

    if (url.pathname === '/chat') {
      const query = String(body.query || '').trim();
      const context = String(body.context || '');
      if (!query) return json({ error: 'query required' }, 400, cors);
      if (query.length > MAX_QUERY_LEN) return json({ error: 'query too long' }, 400, cors);
      if (context.length > MAX_CONTEXT_LEN) return json({ error: 'context too long' }, 400, cors);

      const userText = `Context (retrieved notes):\n\n${context}\n\n---\n\nQuestion: ${query}`;

      const upstream = await fetchWithRetry(
        `https://generativelanguage.googleapis.com/v1beta/models/${CHAT_MODEL}:streamGenerateContent?alt=sse&key=${env.GEMINI_API_KEY}`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
            contents: [{ role: 'user', parts: [{ text: userText }] }],
            generationConfig: { temperature: 0.3 },
          }),
        },
      );
      if (!upstream.ok || !upstream.body) {
        const detail = await upstream.text().catch(() => '');
        return json({ error: 'chat upstream failed', status: upstream.status, detail: detail.slice(0, 500) }, 502, cors);
      }
      return new Response(upstream.body, {
        status: 200,
        headers: {
          ...cors,
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-cache, no-transform',
          'x-accel-buffering': 'no',
        },
      });
    }

    return json({ error: 'not found' }, 404, cors);
  },
};

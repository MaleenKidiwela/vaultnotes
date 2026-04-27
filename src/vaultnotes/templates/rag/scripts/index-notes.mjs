#!/usr/bin/env node
// Build-time RAG indexer. Reads the list of project folders from
// rag-config.json at the repo root (written by vaultnotes), walks each
// for .md files, chunks them, embeds chunks via Gemini, and writes
// public/{search-index.json,chunks.json,embeddings.bin}.

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { remark } from 'remark';
import strip from 'strip-markdown';
import MiniSearch from 'minisearch';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const NOTES_DIR = path.join(ROOT, 'notes');
const OUT_DIR = path.join(ROOT, 'public');
const RAG_CONFIG_PATH = path.join(ROOT, 'rag-config.json');

const DEFAULTS = {
  embedModel: 'gemini-embedding-001',
  embedDim: 768,
  chunkWords: 375,
  overlapWords: 75,
  batchSize: 25,
};
const MAX_RETRIES = 6;
const BASE_BACKOFF_MS = 4000;
const INTER_BATCH_DELAY_MS = 1500;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function loadRagConfig() {
  try {
    const raw = await fs.readFile(RAG_CONFIG_PATH, 'utf8');
    const j = JSON.parse(raw);
    return {
      folders: Array.isArray(j.folders) ? j.folders : [],
      embedModel: j.embedModel || DEFAULTS.embedModel,
      embedDim: j.embedDim || DEFAULTS.embedDim,
      chunkWords: j.chunkWords || DEFAULTS.chunkWords,
      overlapWords: j.overlapWords || DEFAULTS.overlapWords,
      batchSize: j.batchSize || DEFAULTS.batchSize,
    };
  } catch (e) {
    throw new Error(`rag-config.json missing or invalid at ${RAG_CONFIG_PATH}: ${e.message}`);
  }
}

async function walk(dir) {
  const out = [];
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (e) {
    if (e.code === 'ENOENT') return out;
    throw e;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...await walk(full));
    else if (e.isFile() && e.name.toLowerCase().endsWith('.md')) out.push(full);
  }
  return out;
}

function chunkWordsFn(text, chunkSize, overlap) {
  const words = text.split(/\s+/).filter(Boolean);
  const chunks = [];
  if (!words.length) return chunks;
  if (words.length <= chunkSize) {
    chunks.push(words.join(' '));
    return chunks;
  }
  const step = chunkSize - overlap;
  for (let i = 0; i < words.length; i += step) {
    chunks.push(words.slice(i, i + chunkSize).join(' '));
    if (i + chunkSize >= words.length) break;
  }
  return chunks;
}

function l2norm(values) {
  let s = 0;
  for (const v of values) s += v * v;
  s = Math.sqrt(s) || 1;
  const out = new Float32Array(values.length);
  for (let i = 0; i < values.length; i++) out[i] = values[i] / s;
  return out;
}

function urlForRelPath(rel) {
  return '/' + rel.split(path.sep).map(encodeURIComponent).join('/');
}

async function embedBatch(texts, apiKey, cfg) {
  const body = {
    requests: texts.map((t) => ({
      model: `models/${cfg.embedModel}`,
      content: { parts: [{ text: t }] },
      outputDimensionality: cfg.embedDim,
      taskType: 'RETRIEVAL_DOCUMENT',
    })),
  };
  let lastErr;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${cfg.embedModel}:batchEmbedContents?key=${apiKey}`,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      },
    );
    if (res.ok) {
      const j = await res.json();
      return j.embeddings.map((e) => e.values);
    }
    const txt = await res.text().catch(() => '');
    lastErr = new Error(`Embed batch failed (${res.status}): ${txt.slice(0, 500)}`);
    if (res.status !== 429 && res.status < 500) throw lastErr;
    if (attempt === MAX_RETRIES) throw lastErr;
    let wait = BASE_BACKOFF_MS * Math.pow(2, attempt);
    const ra = res.headers.get('retry-after');
    if (ra && !Number.isNaN(parseInt(ra, 10))) wait = Math.max(wait, parseInt(ra, 10) * 1000);
    console.warn(`  ${res.status} from embed API; retrying in ${Math.round(wait / 1000)}s (attempt ${attempt + 1}/${MAX_RETRIES})`);
    await sleep(wait);
  }
  throw lastErr;
}

async function main() {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    console.error('GEMINI_API_KEY not set; aborting.');
    process.exit(1);
  }

  const cfg = await loadRagConfig();
  if (!cfg.folders.length) {
    console.error('rag-config.json has no folders. Run `vaultnotes sync` to refresh it.');
    process.exit(1);
  }

  const files = [];
  for (const folder of cfg.folders) {
    files.push(...await walk(path.join(NOTES_DIR, folder)));
  }
  files.sort();
  console.log(`Found ${files.length} markdown files across ${cfg.folders.join(', ')}.`);

  const stripper = remark().use(strip);
  const chunks = [];
  let totalWords = 0;

  for (const file of files) {
    const raw = await fs.readFile(file, 'utf8');
    const { data, content } = matter(raw);
    const plain = String(await stripper.process(content)).replace(/\s+/g, ' ').trim();
    if (!plain) continue;
    const rel = path.relative(ROOT, file);
    const noteTitle = data.title || path.basename(file, path.extname(file));
    const noteUrl = urlForRelPath(rel);
    const pieces = chunkWordsFn(plain, cfg.chunkWords, cfg.overlapWords);
    pieces.forEach((text, idx) => {
      totalWords += text.split(/\s+/).length;
      chunks.push({
        id: chunks.length,
        noteId: rel,
        noteTitle,
        noteUrl,
        chunkIndex: idx,
        text,
      });
    });
  }

  console.log(`Chunked into ${chunks.length} pieces (~${Math.round(totalWords / 0.75)} tokens).`);
  await fs.mkdir(OUT_DIR, { recursive: true });

  if (chunks.length === 0) {
    console.warn('No content to index. Writing empty artifacts.');
    const mini = new MiniSearch({
      fields: ['text', 'noteTitle'],
      storeFields: ['noteTitle', 'noteUrl', 'chunkIndex', 'noteId'],
      idField: 'id',
    });
    await fs.writeFile(path.join(OUT_DIR, 'search-index.json'), JSON.stringify(mini));
    await fs.writeFile(path.join(OUT_DIR, 'chunks.json'), '[]');
    await fs.writeFile(path.join(OUT_DIR, 'embeddings.bin'), Buffer.alloc(0));
    return;
  }

  const embeddings = new Float32Array(chunks.length * cfg.embedDim);
  let calls = 0;
  for (let b = 0; b < chunks.length; b += cfg.batchSize) {
    const batch = chunks.slice(b, b + cfg.batchSize);
    const vectors = await embedBatch(batch.map((c) => c.text), apiKey, cfg);
    calls++;
    vectors.forEach((vec, i) => {
      const norm = l2norm(vec);
      embeddings.set(norm, (b + i) * cfg.embedDim);
    });
    console.log(`  embedded ${Math.min(b + cfg.batchSize, chunks.length)}/${chunks.length}`);
    if (b + cfg.batchSize < chunks.length) await sleep(INTER_BATCH_DELAY_MS);
  }

  const mini = new MiniSearch({
    fields: ['text', 'noteTitle'],
    storeFields: ['noteTitle', 'noteUrl', 'chunkIndex', 'noteId'],
    idField: 'id',
  });
  mini.addAll(chunks);

  await fs.writeFile(path.join(OUT_DIR, 'search-index.json'), JSON.stringify(mini));
  await fs.writeFile(path.join(OUT_DIR, 'chunks.json'), JSON.stringify(chunks));
  await fs.writeFile(
    path.join(OUT_DIR, 'embeddings.bin'),
    Buffer.from(embeddings.buffer, embeddings.byteOffset, embeddings.byteLength),
  );

  console.log(
    `Done. notes=${files.length} chunks=${chunks.length} ~tokens=${Math.round(totalWords / 0.75)} embed_calls=${calls} bytes=${chunks.length * cfg.embedDim * 4}`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

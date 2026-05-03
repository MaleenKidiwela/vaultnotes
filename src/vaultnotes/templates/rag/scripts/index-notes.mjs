#!/usr/bin/env node
// Build-time RAG indexer. Reads the list of project folders from
// rag-config.json at the repo root (written by vaultnotes), walks each
// for .md files, chunks them, embeds chunks via Gemini, and writes
// public/{search-index.json,chunks.json,embeddings.bin}.

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';
import matter from 'gray-matter';
import { remark } from 'remark';
import remarkParse from 'remark-parse';
import MiniSearch from 'minisearch';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const NOTES_DIR = path.join(ROOT, 'notes');
const OUT_DIR = path.join(ROOT, 'public');
const RAG_CONFIG_PATH = path.join(ROOT, 'rag-config.json');
const CHUNKS_PATH = path.join(OUT_DIR, 'chunks.json');
const EMBEDDINGS_PATH = path.join(OUT_DIR, 'embeddings.bin');

const DEFAULTS = {
  embedModel: 'gemini-embedding-001',
  embedDim: 768,
  chunkWords: 500,
  overlapWords: 100,
  minChunkWords: 180,
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
      minChunkWords: j.minChunkWords || DEFAULTS.minChunkWords,
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

function splitWithOverlap(text, maxWords, overlapWords) {
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length <= maxWords) return [words.join(' ')];
  const step = Math.max(1, maxWords - overlapWords);
  const out = [];
  for (let i = 0; i < words.length; i += step) {
    out.push(words.slice(i, i + maxWords).join(' '));
    if (i + maxWords >= words.length) break;
  }
  return out;
}

function wordCount(text) {
  return text.split(/\s+/).filter(Boolean).length;
}

function dateFromFilename(filename) {
  const name = path.basename(filename);
  let m = name.match(/(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})/);
  if (m) {
    const month = parseInt(m[2], 10);
    const day = parseInt(m[3], 10);
    if (month >= 1 && month <= 12 && day >= 1 && day <= 31) return `${m[1]}-${m[2]}-${m[3]}`;
  }
  m = name.match(/\b(\d{2})-(\d{2})-(\d{2}|\d{4})\b/);
  if (m) {
    const month = parseInt(m[1], 10);
    const day = parseInt(m[2], 10);
    if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      const year = m[3].length === 2 ? `20${m[3]}` : m[3];
      return `${year}-${m[1]}-${m[2]}`;
    }
  }
  return '';
}

function flattenNode(node) {
  if (!node) return '';
  if (typeof node.value === 'string') return node.value;
  if (Array.isArray(node.children)) {
    const blockTypes = new Set([
      'paragraph', 'heading', 'listItem', 'blockquote',
      'tableRow', 'tableCell', 'definition', 'footnoteDefinition',
    ]);
    const sep = blockTypes.has(node.type) ? ' ' : '\n';
    return node.children.map(flattenNode).join(sep);
  }
  return '';
}

function flattenNodes(nodes) {
  return nodes
    .map(flattenNode)
    .join('\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

function splitIntoSections(tree) {
  const sections = [];
  const headingStack = [];
  let current = { headingPath: [], nodes: [] };
  for (const node of tree.children || []) {
    if (node.type === 'heading') {
      if (current.nodes.length || current.headingPath.length) sections.push(current);
      while (headingStack.length && headingStack[headingStack.length - 1].depth >= node.depth) {
        headingStack.pop();
      }
      const title = flattenNode(node).trim();
      headingStack.push({ depth: node.depth, title });
      current = { headingPath: headingStack.map((h) => ({ ...h })), nodes: [] };
    } else {
      current.nodes.push(node);
    }
  }
  if (current.nodes.length || current.headingPath.length) sections.push(current);
  return sections;
}

function rootSection(sectionPath) {
  return sectionPath.length ? sectionPath[0] : '';
}

function chunkHeader({ project, noteTitle, sectionPath, date, rel }) {
  return [
    `Project: ${project}`,
    `Note: ${noteTitle}`,
    sectionPath ? `Section: ${sectionPath}` : '',
    date ? `Date: ${date}` : '',
    `File: ${rel}`,
  ].filter(Boolean).join('\n');
}

function embeddingInputForChunk(chunk) {
  const header = chunkHeader({
    project: chunk.project,
    noteTitle: chunk.noteTitle,
    sectionPath: chunk.sectionPath || chunk.sectionTitle,
    date: chunk.date || chunk.dateFromFilename,
    rel: chunk.filePath || chunk.noteId,
  });
  return `${header}\n\n${chunk.text}`;
}

function noteTitleFromTree(tree, frontmatterTitle, fallback) {
  if (frontmatterTitle) return String(frontmatterTitle);
  const h1 = (tree.children || []).find((n) => n.type === 'heading' && n.depth === 1);
  if (h1) {
    const title = flattenNode(h1).trim();
    if (title) return title;
  }
  return fallback;
}

function buildChunksForNote(sections, meta, cfg) {
  const draft = [];
  let sectionOrdinal = 0;

  for (const section of sections) {
    const text = flattenNodes(section.nodes);
    if (!text) continue;
    const sectionPath = section.headingPath.map((h) => h.title).filter(Boolean);
    const sectionTitle = sectionPath.length ? sectionPath[sectionPath.length - 1] : meta.noteTitle;
    const sectionWords = wordCount(text);
    const sectionId = `${meta.noteId}#${sectionOrdinal++}`;

    if (sectionWords <= cfg.chunkWords) {
      draft.push({ sectionTitle, sectionPath, sectionId, text });
    } else {
      const pieces = splitWithOverlap(text, cfg.chunkWords, cfg.overlapWords);
      for (const piece of pieces) draft.push({ sectionTitle, sectionPath, sectionId, text: piece });
    }
  }

  const merged = [];
  for (const chunk of draft) {
    const prev = merged[merged.length - 1];
    const sameRoot = prev && rootSection(prev.sectionPath) === rootSection(chunk.sectionPath);
    if (wordCount(chunk.text) < cfg.minChunkWords && prev && sameRoot) {
      prev.text = `${prev.text}\n${chunk.text}`.trim();
    } else {
      merged.push({ ...chunk });
    }
  }

  if (merged.length >= 2) {
    const last = merged[merged.length - 1];
    const prev = merged[merged.length - 2];
    if (wordCount(last.text) < cfg.minChunkWords && rootSection(last.sectionPath) === rootSection(prev.sectionPath)) {
      prev.text = `${prev.text}\n${last.text}`.trim();
      merged.pop();
    }
  }

  const chunks = merged.map((chunk, idx) => ({
    ...meta,
    chunkIndex: idx,
    sectionTitle: chunk.sectionTitle || meta.noteTitle,
    sectionPath: chunk.sectionPath,
    sectionId: chunk.sectionId,
    text: chunk.text,
  }));

  const sectionCounts = new Map();
  for (const chunk of chunks) {
    sectionCounts.set(chunk.sectionId, (sectionCounts.get(chunk.sectionId) || 0) + 1);
  }
  const sectionIndexes = new Map();

  return chunks.map((chunk, idx) => {
    const sectionChunkIndex = sectionIndexes.get(chunk.sectionId) || 0;
    sectionIndexes.set(chunk.sectionId, sectionChunkIndex + 1);
    const sectionPath = Array.isArray(chunk.sectionPath) && chunk.sectionPath.length
      ? chunk.sectionPath.join(' / ')
      : chunk.sectionTitle;
    const header = chunkHeader({
      project: chunk.project,
      noteTitle: chunk.noteTitle,
      sectionPath,
      date: chunk.date,
      rel: chunk.filePath,
    });
    const embedText = `${header}\n\n${chunk.text}`;
    return {
      noteId: chunk.noteId,
      noteTitle: chunk.noteTitle,
      noteUrl: chunk.noteUrl,
      project: chunk.project,
      date: chunk.date,
      dateFromFilename: chunk.date,
      sectionTitle: chunk.sectionTitle,
      sectionPath,
      filePath: chunk.filePath,
      sectionId: chunk.sectionId,
      sectionChunkIndex,
      sectionChunkCount: sectionCounts.get(chunk.sectionId) || 1,
      chunkIndex: idx,
      text: chunk.text,
      searchText: `${chunk.noteTitle} ${chunk.project} ${chunk.date} ${chunk.filePath} ${sectionPath} ${chunk.text}`.trim(),
      embedText,
    };
  });
}

function l2norm(values) {
  let s = 0;
  for (const v of values) s += v * v;
  s = Math.sqrt(s) || 1;
  const out = new Float32Array(values.length);
  for (let i = 0; i < values.length; i++) out[i] = values[i] / s;
  return out;
}

function embeddingCacheKey(text, cfg) {
  const hash = crypto.createHash('sha256').update(text).digest('hex');
  return `${cfg.embedModel}:${cfg.embedDim}:RETRIEVAL_DOCUMENT:${hash}`;
}

async function loadEmbeddingCache(cfg) {
  try {
    const [chunksRaw, embBuf] = await Promise.all([
      fs.readFile(CHUNKS_PATH, 'utf8'),
      fs.readFile(EMBEDDINGS_PATH),
    ]);
    const cachedChunks = JSON.parse(chunksRaw);
    if (!Array.isArray(cachedChunks)) return new Map();

    const cachedVectors = new Float32Array(embBuf.buffer, embBuf.byteOffset, embBuf.byteLength / Float32Array.BYTES_PER_ELEMENT);
    if (cachedVectors.length !== cachedChunks.length * cfg.embedDim) {
      console.warn('Embedding cache ignored: chunks/embeddings length mismatch.');
      return new Map();
    }

    const cache = new Map();
    for (let i = 0; i < cachedChunks.length; i++) {
      if (!cachedChunks[i]?.text) continue;
      const text = cachedChunks[i].embedText || embeddingInputForChunk(cachedChunks[i]);
      const start = i * cfg.embedDim;
      const vector = cachedVectors.slice(start, start + cfg.embedDim);
      cache.set(embeddingCacheKey(text, cfg), vector);
    }
    console.log(`Loaded embedding cache with ${cache.size} vectors.`);
    return cache;
  } catch (e) {
    if (e.code !== 'ENOENT') console.warn(`Embedding cache unavailable: ${e.message}`);
    return new Map();
  }
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

  const parser = remark().use(remarkParse);
  const chunks = [];
  let totalWords = 0;

  for (const file of files) {
    const raw = await fs.readFile(file, 'utf8');
    const { data, content } = matter(raw);
    const tree = parser.parse(content);
    const rel = path.relative(ROOT, file);
    const project = path.relative(NOTES_DIR, file).split(path.sep)[0] || '';
    const noteTitle = noteTitleFromTree(tree, data.title, path.basename(file, path.extname(file)));
    const date = dateFromFilename(file);
    const meta = {
      noteId: rel,
      noteTitle,
      noteUrl: urlForRelPath(rel),
      project,
      date,
      filePath: rel,
    };
    const sections = splitIntoSections(tree);
    const noteChunks = buildChunksForNote(sections, meta, cfg);
    for (const chunk of noteChunks) {
      totalWords += wordCount(chunk.text);
      chunks.push({ id: chunks.length, ...chunk });
    }
  }

  console.log(`Chunked into ${chunks.length} pieces (~${Math.round(totalWords / 0.75)} tokens).`);
  await fs.mkdir(OUT_DIR, { recursive: true });

  if (chunks.length === 0) {
    console.warn('No content to index. Writing empty artifacts.');
    const mini = new MiniSearch({
      fields: ['searchText', 'noteTitle', 'sectionTitle', 'sectionPath', 'filePath', 'project', 'date', 'dateFromFilename'],
      storeFields: [
        'noteTitle', 'noteUrl', 'chunkIndex', 'noteId', 'sectionTitle', 'sectionPath', 'filePath',
        'sectionId', 'sectionChunkIndex', 'sectionChunkCount', 'project', 'date', 'dateFromFilename',
      ],
      idField: 'id',
    });
    await fs.writeFile(path.join(OUT_DIR, 'search-index.json'), JSON.stringify(mini));
    await fs.writeFile(path.join(OUT_DIR, 'chunks.json'), '[]');
    await fs.writeFile(path.join(OUT_DIR, 'embeddings.bin'), Buffer.alloc(0));
    return;
  }

  const embeddings = new Float32Array(chunks.length * cfg.embedDim);
  const embeddingCache = await loadEmbeddingCache(cfg);
  const missing = [];
  let cacheHits = 0;
  for (const chunk of chunks) {
    const key = embeddingCacheKey(chunk.embedText || chunk.text, cfg);
    const cached = embeddingCache.get(key);
    if (cached && cached.length === cfg.embedDim) {
      embeddings.set(cached, chunk.id * cfg.embedDim);
      cacheHits++;
    } else {
      missing.push({ chunk, key });
    }
  }

  let calls = 0;
  if (missing.length && !apiKey) {
    console.error(`GEMINI_API_KEY not set; ${missing.length} uncached chunks need embeddings.`);
    process.exit(1);
  }
  for (let b = 0; b < missing.length; b += cfg.batchSize) {
    const batch = missing.slice(b, b + cfg.batchSize);
    const vectors = await embedBatch(batch.map((item) => item.chunk.embedText || item.chunk.text), apiKey, cfg);
    calls++;
    vectors.forEach((vec, i) => {
      const norm = l2norm(vec);
      const item = batch[i];
      embeddings.set(norm, item.chunk.id * cfg.embedDim);
      embeddingCache.set(item.key, norm);
    });
    console.log(`  embedded ${Math.min(b + cfg.batchSize, missing.length)}/${missing.length} uncached chunks`);
    if (b + cfg.batchSize < missing.length) await sleep(INTER_BATCH_DELAY_MS);
  }

  const mini = new MiniSearch({
    fields: ['searchText', 'noteTitle', 'sectionTitle', 'sectionPath', 'filePath', 'project', 'date', 'dateFromFilename'],
    storeFields: [
      'noteTitle', 'noteUrl', 'chunkIndex', 'noteId', 'sectionTitle', 'sectionPath', 'filePath',
      'sectionId', 'sectionChunkIndex', 'sectionChunkCount', 'project', 'date', 'dateFromFilename',
    ],
    idField: 'id',
  });
  mini.addAll(chunks);

  const publicChunks = chunks.map(({ embedText, searchText, ...chunk }) => chunk);

  await fs.writeFile(path.join(OUT_DIR, 'search-index.json'), JSON.stringify(mini));
  await fs.writeFile(path.join(OUT_DIR, 'chunks.json'), JSON.stringify(publicChunks));
  await fs.writeFile(
    path.join(OUT_DIR, 'embeddings.bin'),
    Buffer.from(embeddings.buffer, embeddings.byteOffset, embeddings.byteLength),
  );

  console.log(
    `Done. notes=${files.length} chunks=${chunks.length} cache_hits=${cacheHits} embedded=${missing.length} ~tokens=${Math.round(totalWords / 0.75)} embed_calls=${calls} bytes=${chunks.length * cfg.embedDim * 4}`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

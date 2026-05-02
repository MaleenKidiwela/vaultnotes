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
import remarkParse from 'remark-parse';
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

function wordCount(text) {
  return text.split(/\s+/).filter(Boolean).length;
}

function dateFromFilename(filename) {
  const m = path.basename(filename).match(/^(\d{2})-(\d{2})-(\d{2}|\d{4})\s+Notes\.md$/i);
  if (!m) return '';
  const year = m[3].length === 2 ? `20${m[3]}` : m[3];
  return `${year}-${m[1]}-${m[2]}`;
}

function nodeText(node) {
  if (!node) return '';
  if (typeof node.value === 'string') return node.value;
  if (!Array.isArray(node.children)) return '';
  return node.children.map(nodeText).join(' ').replace(/\s+/g, ' ').trim();
}

function fallbackSections(markdown) {
  const sections = [];
  let current = { heading: '', level: 0, sectionPath: [], lines: [] };
  const stack = [];

  for (const line of markdown.split(/\r?\n/)) {
    const m = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (m) {
      if (current.lines.join('\n').trim()) sections.push(current);
      const level = m[1].length;
      stack.length = level - 1;
      stack[level - 1] = m[2].trim();
      current = {
        heading: m[2].trim(),
        level,
        sectionPath: stack.filter(Boolean),
        markdown: '',
        lines: [],
      };
    } else {
      current.lines.push(line);
    }
  }
  if (current.lines.join('\n').trim()) sections.push(current);
  return sections.length
    ? sections.map((s) => ({ ...s, markdown: s.lines.join('\n') }))
    : [{ heading: 'Overview', level: 0, sectionPath: ['Overview'], markdown }];
}

function splitIntoSections(markdown) {
  let tree;
  try {
    tree = remark().use(remarkParse).parse(markdown);
  } catch {
    return fallbackSections(markdown);
  }

  const starts = [];
  const stack = [];
  for (const node of tree.children || []) {
    if (node.type !== 'heading') continue;
    const start = node.position?.start?.offset;
    if (!Number.isInteger(start)) return fallbackSections(markdown);
    const heading = nodeText(node) || 'Untitled section';
    const level = node.depth || 1;
    stack.length = level - 1;
    stack[level - 1] = heading;
    starts.push({
      start,
      heading,
      level,
      sectionPath: stack.filter(Boolean),
    });
  }

  const sections = [];
  const firstContent = starts[0]?.start ?? markdown.length;
  if (markdown.slice(0, firstContent).trim()) {
    sections.push({
      heading: 'Overview',
      level: 0,
      sectionPath: ['Overview'],
      markdown: markdown.slice(0, firstContent),
    });
  }
  for (let i = 0; i < starts.length; i++) {
    const section = starts[i];
    const end = starts[i + 1]?.start ?? markdown.length;
    const body = markdown.slice(section.start, end);
    if (!body.trim()) continue;
    sections.push({ ...section, markdown: body });
  }

  return sections.length ? sections : [{ heading: 'Overview', level: 0, sectionPath: ['Overview'], markdown }];
}

async function sectionPlainText(section, stripper) {
  return String(await stripper.process(section.markdown)).replace(/\s+/g, ' ').trim();
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

async function buildNoteChunks({ file, rel, project, noteTitle, noteUrl, stripper, cfg }) {
  const raw = await fs.readFile(file, 'utf8');
  const { content } = matter(raw);
  const sections = splitIntoSections(content);
  const date = dateFromFilename(file);
  const chunks = [];
  let carry = null;
  let sectionOrdinal = 0;

  for (const section of sections) {
    const plain = await sectionPlainText(section, stripper);
    if (!plain) continue;

    const sectionTitle = section.heading || 'Overview';
    const sectionPath = (section.sectionPath && section.sectionPath.length)
      ? section.sectionPath.join(' / ')
      : sectionTitle;
    const sectionId = `${rel}#${sectionOrdinal++}`;
    const sectionWords = wordCount(plain);

    if (sectionWords < cfg.minChunkWords) {
      if (carry) {
        carry.text = `${carry.text}\n\n${sectionTitle}: ${plain}`;
        carry.sectionTitle = `${carry.sectionTitle}; ${sectionTitle}`;
        carry.sectionPath = `${carry.sectionPath}; ${sectionPath}`;
      } else {
        carry = { sectionTitle, sectionPath, sectionId, text: `${sectionTitle}: ${plain}` };
      }
      if (wordCount(carry.text) < cfg.chunkWords) continue;
    }

    if (carry) {
      chunks.push({
        sectionTitle: carry.sectionTitle,
        sectionPath: carry.sectionPath,
        sectionId: carry.sectionId,
        text: carry.text,
      });
      carry = null;
    }

    if (sectionWords >= cfg.minChunkWords) {
      const pieces = chunkWordsFn(plain, cfg.chunkWords, cfg.overlapWords);
      for (const text of pieces) chunks.push({ sectionTitle, sectionPath, sectionId, text });
    }
  }

  if (carry) {
    chunks.push({
      sectionTitle: carry.sectionTitle,
      sectionPath: carry.sectionPath,
      sectionId: carry.sectionId,
      text: carry.text,
    });
  }

  const sectionCounts = new Map();
  for (const chunk of chunks) {
    sectionCounts.set(chunk.sectionId, (sectionCounts.get(chunk.sectionId) || 0) + 1);
  }
  const sectionIndexes = new Map();

  return chunks.map((chunk, idx) => {
    const sectionChunkIndex = sectionIndexes.get(chunk.sectionId) || 0;
    sectionIndexes.set(chunk.sectionId, sectionChunkIndex + 1);
    const header = chunkHeader({
      project,
      noteTitle,
      sectionPath: chunk.sectionPath || chunk.sectionTitle,
      date,
      rel,
    });
    const text = `${header}\n\n${chunk.text}`;
    return {
      noteId: rel,
      noteTitle,
      noteUrl,
      project,
      date,
      sectionTitle: chunk.sectionTitle,
      sectionPath: chunk.sectionPath || chunk.sectionTitle,
      filePath: rel,
      sectionId: chunk.sectionId,
      sectionChunkIndex,
      sectionChunkCount: sectionCounts.get(chunk.sectionId) || 1,
      chunkIndex: idx,
      text,
      searchText: `${noteTitle} ${project} ${date} ${rel} ${chunk.sectionPath || chunk.sectionTitle} ${chunk.text}`.trim(),
      embedText: text,
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
    const rel = path.relative(ROOT, file);
    const project = path.relative(NOTES_DIR, file).split(path.sep)[0] || '';
    const firstHeading = (content.match(/^#\s+(.+?)\s*#*\s*$/m) || [])[1];
    const noteTitle = data.title || firstHeading || path.basename(file, path.extname(file));
    const noteUrl = urlForRelPath(rel);
    const noteChunks = await buildNoteChunks({
      file,
      rel,
      project,
      noteTitle,
      noteUrl,
      stripper,
      cfg,
    });
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
      fields: ['searchText', 'noteTitle', 'sectionTitle', 'sectionPath', 'filePath', 'project', 'date'],
      storeFields: [
        'noteTitle', 'noteUrl', 'chunkIndex', 'noteId', 'sectionTitle', 'sectionPath', 'filePath',
        'sectionId', 'sectionChunkIndex', 'sectionChunkCount', 'project', 'date',
      ],
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
    const vectors = await embedBatch(batch.map((c) => c.embedText || c.text), apiKey, cfg);
    calls++;
    vectors.forEach((vec, i) => {
      const norm = l2norm(vec);
      embeddings.set(norm, (b + i) * cfg.embedDim);
    });
    console.log(`  embedded ${Math.min(b + cfg.batchSize, chunks.length)}/${chunks.length}`);
    if (b + cfg.batchSize < chunks.length) await sleep(INTER_BATCH_DELAY_MS);
  }

  const mini = new MiniSearch({
    fields: ['searchText', 'noteTitle', 'sectionTitle', 'sectionPath', 'filePath', 'project', 'date'],
    storeFields: [
      'noteTitle', 'noteUrl', 'chunkIndex', 'noteId', 'sectionTitle', 'sectionPath', 'filePath',
      'sectionId', 'sectionChunkIndex', 'sectionChunkCount', 'project', 'date',
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
    `Done. notes=${files.length} chunks=${chunks.length} ~tokens=${Math.round(totalWords / 0.75)} embed_calls=${calls} bytes=${chunks.length * cfg.embedDim * 4}`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

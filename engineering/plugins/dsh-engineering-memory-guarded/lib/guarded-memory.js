import { createHash, randomUUID } from 'node:crypto';
import { appendFileSync, closeSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const ID = /^[\p{L}_][\p{L}\p{N}_.-]{0,79}$/u;
const VALUE = /^[+-]?(?:\d{1,20}(?:\.\d{1,12})?|\.\d{1,12})\s?(?:mm|cm|m|in|°|deg|N|kN|Pa|MPa|GPa|kg|g|rpm|%)?$/u;
const SHA256 = /^[a-f0-9]{64}$/;
export const MARKER = 'DSH-ENG-MEMORY-V2 ';

export const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const messageText = message => (message?.content ?? [])
  .filter(block => block.type === 'text').map(block => block.text).join('\n');

function read(path) {
  let bytes;
  try { bytes = readFileSync(path); }
  catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  return JSON.parse(text);
}

function put(path, value) {
  const temp = path + '.' + randomUUID() + '.tmp';
  try {
    const fd = openSync(temp, 'wx', 0o600);
    try {
      writeFileSync(fd, JSON.stringify(value), 'utf8');
      fsyncSync(fd);
    } finally { closeSync(fd); }
    renameSync(temp, path);
  } finally { rmSync(temp, { force: true }); }
}

export function openJournal(sessionDirectory, session) {
  const directory = join(sessionDirectory, '.engineering-compaction-v2', digest(session));
  const index = join(directory, 'agent.json');
  const previous = join(directory, 'agent.previous.json');
  function readState(path) {
    const record = read(path);
    if (record === undefined) return undefined;
    if (record.state?.session !== session || record.state?.version !== 2 || digest(record.state) !== record.hash) {
      throw new Error('MEMORY_AGENT_RECORD_INVALID');
    }
    return record;
  }
  function backupPath(hash) {
    if (!SHA256.test(hash)) throw new Error('MEMORY_HASH_INVALID');
    return join(directory, hash + '.json');
  }
  function loadRecord(hash) {
    const record = read(backupPath(hash));
    if (!record || record.session !== session || digest(record) !== hash) throw new Error('MEMORY_BACKUP_HASH_MISMATCH');
    return record;
  }
  function load(hash) { return loadRecord(hash).data; }
  return {
    directory,
    state() {
      let primaryError;
      try {
        const record = readState(index);
        if (record) return record.state;
      } catch (error) { primaryError = error; }
      const fallback = readState(previous);
      if (fallback) {
        const state = fallback.state;
        return { ...state, pending: {
          id: 'recovery-' + fallback.hash, graph: state.graph, changes: [], consumed: [], range: null,
          ...state.pending, failure: 'MEMORY_AGENT_RECORD_RESTORED',
        } };
      }
      if (primaryError) throw primaryError;
      return { version: 2, session, graph: {}, pending: null, pins: [], consumed: [] };
    },
    save(state) {
      if (state.session !== session) throw new Error('MEMORY_WRONG_SESSION');
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      let current;
      try { current = readState(index); } catch { /* preserve the last verified previous record */ }
      if (current) put(previous, current);
      put(index, { state, hash: digest(state) });
    },
    /**
     * 写一份不可变备份，返回内容哈希。
     *
     * extra 是【附加证据】字段（例如 sourceRefs / label / hostId），它**不参与**
     * 主体 data 的哈希计算之外的校验口径：record = { session, data, ...extra }，
     * 哈希对整条 record 计算，所以附加字段同样被 SHA-256 保护 —— 篡改任何一位
     * 都会让 load() 抛 MEMORY_BACKUP_HASH_MISMATCH。
     */
    backup(data, extra) {
      const record = { session, data };
      if (extra && typeof extra === 'object') {
        for (const key of Object.keys(extra)) {
          if (key === 'session' || key === 'data') continue;
          record[key] = extra[key];
        }
      }
      const hash = digest(record);
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      const file = backupPath(hash);
      const existing = read(file);
      if (existing === undefined) put(file, record);
      load(hash);
      return hash;
    },
    load,
    loadRecord,
    backupPath,
    recordRead(pins) {
      if (pins.length === 0) return;
      appendFileSync(join(directory, 'reads.jsonl'), JSON.stringify({ at: Date.now(), session,
        checkpoints: pins.map(({ attempt, message, hash }) => ({ attempt, message, hash })),
      }) + '\n', { encoding: 'utf8', mode: 0o600 });
    },
  };
}

/**
 * 识别用户消息里"明确写出的尺寸变更"。
 *
 * 支持两种写法（教程第 3 节）：
 *   1. !尺寸 shaft.diameter = 20 mm
 *   2. 请把 shaft.diameter 改为 24 mm。 / 把 shaft.diameter 改成 24 mm
 * 一条消息可以分行登记多项 —— 这里按 [换行 。 ； ; ? ？] 切分后逐条匹配。
 */
export function directChanges(text) {
  const changes = [];
  for (const clause of String(text).split(/[\n。；;?？]+/u)) {
    const line = clause.trim();
    const match = /^(?:[!！]尺寸\s+([^\s=]+)\s*=\s*(.+)|(?:请)?(?:把|将)\s*([^\s]+?)\s*(?:改为|改成|设为|设成)\s*(.+))$/u.exec(line);
    if (!match) continue;
    const id = match[1] ?? match[3];
    const value = (match[2] ?? match[4]).trim();
    if (ID.test(id) && VALUE.test(value)) changes.push({ kind: 'user', id, value });
  }
  return changes;
}

/**
 * 用户"我要换尺寸"这类简短表述。
 *
 * 教程第 5 节要求："插件识别这类简短表述并暂停工程工具，模型可以追问尺寸
 * ID、数值和单位。用户给出 !尺寸 ... 后，先完成压缩和复核再放行。"
 */
export function needsDimensionIntent(text) {
  const s = String(text).trim();
  if (!s) return false;
  if (s === '!继续原尺寸') return true;
  return /(?:我要|我想|需要|打算|准备|想)(?:换|改|调整|变更)(?:一下|下)?(?:尺寸|规格|参数|直径|长度|壁厚)/u.test(s)
    || /^(?:换|改|调整)一下?(?:尺寸|规格|参数)$/u.test(s);
}

export function isResumeOriginal(text) {
  return String(text).trim() === '!继续原尺寸';
}

export function applyChange(graph, change) {
  const { id, kind, value } = change;
  if (typeof id !== 'string' || !ID.test(id) || !['user', 'derive', 'invalidate'].includes(kind)) {
    throw new Error('MEMORY_CHANGE_INVALID');
  }
  const old = Object.hasOwn(graph, id) ? graph[id] : undefined;
  if (old?.source === 'user' && kind !== 'user') throw new Error('MEMORY_USER_CONSTRAINT_PROTECTED');
  if (kind !== 'invalidate' && (typeof value !== 'string' || !VALUE.test(value))) throw new Error('MEMORY_VALUE_INVALID');
  if (kind === 'invalidate' && old === undefined) throw new Error('MEMORY_NODE_UNKNOWN');
  const removed = new Set([id]);
  // 依赖图很小（一个会话里的尺寸节点通常在几十个量级），这里直接扫；
  // 若将来出现大图热点，再补反向边索引。
  for (let grew = true; grew;) {
    grew = false;
    for (const [key, node] of Object.entries(graph)) {
      if (!removed.has(key) && node.parents.some(parent => removed.has(parent))) {
        removed.add(key);
        grew = true;
      }
    }
  }
  const parents = kind === 'derive' ? change.parents : [];
  if (!Array.isArray(parents) || parents.length > 32 || (kind === 'derive' && parents.length === 0) ||
      new Set(parents).size !== parents.length || parents.some(parent => !Object.hasOwn(graph, parent) || removed.has(parent))) {
    throw new Error('MEMORY_DEPENDENCY_INVALID');
  }
  const entries = Object.entries(graph).filter(([key]) => !removed.has(key));
  if (kind !== 'invalidate') entries.push([id, {
    value, parents, source: kind === 'user' ? 'user' : 'derived', ...(change.evidence ? { evidence: change.evidence } : {}),
  }]);
  return Object.fromEntries(entries);
}

export function snapshot(attempt, graph, pages) {
  const constraints = Object.entries(graph).filter(([, node]) => node.source === 'user');
  const derived = Object.entries(graph).filter(([, node]) => node.source === 'derived');
  return MARKER + attempt + '\n' +
    JSON.stringify({ activeConstraints: Object.fromEntries(constraints), derived: Object.fromEntries(derived), pages });
}

export function exportGraph(graph) {
  return {
    version: 1,
    nodes: Object.entries(graph).map(([id, node]) => ({ id, ...node })),
    edges: Object.entries(graph).flatMap(([target, node]) => node.parents.map(source => ({ source, target }))),
  };
}

export function readStatus(sessionDirectory, session) {
  const state = openJournal(sessionDirectory, session).state();
  return {
    session, needsDimensions: Boolean(state.needsDimensions),
    pending: state.pending ? { attempt: state.pending.id, failure: state.pending.failure ?? null } : null,
    committed: state.pins.map(({ at, attempt, hash, inputChars, outputChars, constraints }) =>
      ({ at, attempt, hash, inputChars, outputChars, constraints })),
  };
}

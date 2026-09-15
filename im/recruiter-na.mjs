import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import {
  CodexCliRuntime,
  DingTalkStreamBotAdapter,
  FileDedupeStore,
  UnifiedImBridge,
} from '../vendor/codexharness-im/src/index.mjs';
import { handleControlCommand, shouldUseHiringWorkflow } from './router.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
for (const key of ['CODEX_HOME', 'DINGTALK_CLIENT_ID', 'DINGTALK_CLIENT_SECRET']) {
  if (!process.env[key]) throw new Error(`${key} is required`);
}

const progressDir = process.env.RECRUITER_PROGRESS_DIR || path.join(root, 'workspace', '.progress');
const venvPython = process.env.RECRUITER_PYTHON_BIN || path.join(root, '.venv', 'bin', 'python');
const hostTool = path.join(root, 'runtime', 'rn', 'host_tool.py');
const dedupeDir = process.env.RECRUITER_DEDUPE_DIR || path.join(root, 'workspace', '.im-dedupe');
const dedupe = new FileDedupeStore({ dir: dedupeDir });

const runtime = new CodexCliRuntime({
  bin: process.env.CODEX_BIN || 'codex',
  codexHome: process.env.CODEX_HOME,
  cwd: process.env.CODEX_WORKDIR || root,
  sandbox: process.env.CODEX_SANDBOX || 'workspace-write',
  progressDir,
});

function safeProgressFile(messageId) {
  const id = String(messageId || 'message').replace(/[^A-Za-z0-9._-]+/g, '_');
  const dir = path.join(root, 'workspace', '.host-progress');
  fs.mkdirSync(dir, { recursive: true });
  return path.join(dir, `${Date.now()}-${id}.jsonl`);
}

function workflowArgs(envelope) {
  return [
    hostTool, 'workflow', 'handle',
    '--owner', String(envelope.senderId || ''),
    '--conversation-id', String(envelope.targetId || ''),
    '--text', String(envelope.text || ''),
  ];
}

function hostWorkflow(envelope, progress) {
  const progressFile = safeProgressFile(envelope.messageId);
  fs.writeFileSync(progressFile, '', 'utf8');
  return new Promise((resolve) => {
    const child = spawn(venvPython, workflowArgs(envelope), {
      cwd: root,
      env: {
        ...process.env,
        PYTHONPATH: path.join(root, 'runtime'),
        CODEXHARNESS_PROGRESS_FILE: progressFile,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    let offset = 0;
    const drain = async () => {
      try {
        const text = fs.readFileSync(progressFile, 'utf8');
        const chunk = text.slice(offset);
        offset = text.length;
        for (const line of chunk.split('\n').filter(Boolean)) {
          try { await progress?.update?.(JSON.parse(line)); } catch {}
        }
      } catch {}
    };
    const poller = setInterval(() => { void drain(); }, 500);
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => { err += d; });
    child.on('close', async () => {
      clearInterval(poller);
      await drain();
      try { fs.unlinkSync(progressFile); } catch {}
      try { resolve(JSON.parse(out.trim() || '{}')); }
      catch { resolve({ handled: true, error: err || 'workflow parse error', message: '处理招聘需求时出现问题，请稍后重试。' }); }
    });
    child.on('error', async e => {
      clearInterval(poller);
      await drain();
      resolve({ handled: true, error: e.message, message: '处理招聘需求时出现问题，请稍后重试。' });
    });
  });
}

async function finishProgress(responder, progress, text) {
  if (progress?.active && progress?.finish) {
    try {
      const finalized = await progress.finish(text);
      if (finalized) return;
    } catch {}
  }
  await responder.reply?.(text);
}

const bridge = new UnifiedImBridge({
  runtime,
  dedupe: { claim: () => true },
  promptBuilder: envelope => [
    'You are Recruiter-Na v5, a DingTalk recruiting digital employee for hiring managers.',
    'Public capabilities are strictly limited to: (1) start/continue the caller own hiring request, (2) view the caller own hiring progress.',
    'Do not reveal or browse the full profile library, JD library, resume database, or other users hiring requests.',
    'Candidate Decision and repository access are host-side business services; never recreate them with shell or free-form reasoning, and never invent numeric candidate scores.',
    `sender_id: ${envelope.senderId}`,
    `message: ${envelope.text}`,
    'If this message reaches Codex fallback, give only a concise scope-safe reply.',
  ].join('\n'),
});

const channel = new DingTalkStreamBotAdapter({
  clientId: process.env.DINGTALK_CLIENT_ID,
  clientSecret: process.env.DINGTALK_CLIENT_SECRET,
});

await channel.start(async (envelope, responder) => {
  const mid = envelope?.messageId || '?';
  if (!dedupe.claim(mid)) {
    console.log(`[im] duplicate skipped pid=${process.pid} message_id=${mid}`);
    return;
  }
  console.log(`[im] claimed pid=${process.pid} message_id=${mid} owner=${envelope.senderId}`);

  if (await handleControlCommand(envelope, responder)) return;

  if (shouldUseHiringWorkflow(envelope.text)) {
    const progress = responder.progress;
    let cardStarted = false;
    try {
      if (progress?.start) cardStarted = Boolean(await progress.start({ stage: 'HIRING_WORKFLOW', text: '正在处理你的招聘需求…' }));
      const result = await hostWorkflow(envelope, progress);
      const text = result.message || (result.error ? `处理失败：${result.error}` : '已处理。');
      if (cardStarted) await finishProgress(responder, progress, text);
      else await responder.reply?.(text);
      console.log(`[reply] source=hiring-workflow msg=${mid} state=${result.state || '-'} request=${result.request_id || '-'}`);
      return;
    } catch (error) {
      if (cardStarted && progress?.fail) await progress.fail('处理失败，请稍后重试。').catch(() => {});
      else await responder.reply?.('抱歉，处理招聘需求时出现问题，请稍后重试。');
      console.error(`[workflow] failed msg=${mid}: ${error.stack || error}`);
      return;
    }
  }

  // Defensive fallback; ordinary product traffic should never reach this branch.
  try {
    const result = await bridge.handleInbound(envelope, responder);
    console.log(`[reply] source=codex-fallback msg=${mid} skipped=${result.skipped}`);
  } catch (err) {
    console.error(`[error] msg=${mid} err=${err.message}`);
    await responder.reply?.('抱歉，处理请求时出现问题，请稍后重试。');
  }
});

console.log(`Recruiter-Na v5.0.2 DingTalk bridge started; pid=${process.pid}; root=${root}; dedupe=${dedupeDir}; progress=${progressDir}`);

const stop = () => { try { channel.stop(); } finally { process.exit(0); } };
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
process.on('unhandledRejection', error => { console.error(error); process.exit(1); });
process.on('uncaughtException', error => { console.error(error); process.exit(1); });

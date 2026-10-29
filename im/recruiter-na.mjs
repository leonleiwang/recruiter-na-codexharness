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
        // Python 必须按 UTF-8 输出：Windows 上默认按控制台代码页（GBK）编码 stdout，
        // 桥接按 UTF-8 解码后整条回复都会变成乱码。
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8',
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
    child.stderr.on('data', d => {
      err += d;
      // 子进程的运维诊断必须进 journalctl；只留在 err 里等于线上无从判因。
      process.stderr.write(d);
    });
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
      if (result.error) {
        // 内部错误只进 journalctl，不回给用人经理（避免泄露后端实现细节）。
        console.error(`[workflow] msg=${mid} error=${result.error}`);
      }
      let text = result.message || (result.error ? '抱歉，这次没能完成处理，请稍后重试。' : '已处理。');
      const followUpMessage = result.follow_up_message || '';
      if (result.resume_delivery) {
        let directSubmitted = false;
        if (typeof responder.sendFile === 'function') {
          try {
            const receipt = await responder.sendFile({ filePath: result.resume_delivery.file_path, filename: result.resume_delivery.filename });
            // The adapter returns the DingTalk business response. Require an
            // explicit acknowledgement; absence is not evidence of submission.
            const code = receipt?.errcode ?? receipt?.errCode ?? receipt?.code;
            directSubmitted = Boolean(receipt && receipt.success !== false
              && code != null && ['0', 'ok'].includes(String(code).toLowerCase()));
            if (!directSubmitted) console.error('[resume-delivery] submission not acknowledged');
          } catch {
            // Provider errors may contain paths, tokens or resume metadata.
            console.error('[resume-delivery] submission failed');
          }
        }
        // Never publish original resumes through an unauthenticated static URL.
        // An accepted API submission does not prove recipient receipt.
        text += directSubmitted
          ? ' 已提交钉钉文件投递。'
          : ' 已选中该候选人，但简历暂未送达，请稍后重试或联系 HR 安全获取。';
      }
      if (cardStarted) await finishProgress(responder, progress, text);
      else await responder.reply?.(text);
      if (followUpMessage) await responder.reply?.(followUpMessage);
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

console.log(`Recruiter-Na v5.0.2 DingTalk bridge started; source=v0.2.2; pid=${process.pid}; root=${root}; dedupe=${dedupeDir}; progress=${progressDir}`);

const stop = () => { try { channel.stop(); } finally { process.exit(0); } };
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
process.on('unhandledRejection', error => { console.error(error); process.exit(1); });
process.on('uncaughtException', error => { console.error(error); process.exit(1); });

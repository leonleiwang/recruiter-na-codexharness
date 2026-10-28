import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';

// Execute the real entry point with a synthetic host process and adapter.
// No bridge package, tenant config, network or real candidate data is loaded.
async function boot(root, result) {
  let handler;
  const logs = [];
  const source = fs.readFileSync(new URL('./recruiter-na.mjs', import.meta.url), 'utf8')
    .replace(/^import[\s\S]*?;\n/gm, '')
    .replaceAll('import.meta.url', JSON.stringify(new URL('./recruiter-na.mjs', import.meta.url).href));
  const context = {
    fs, path, randomUUID, setInterval, clearInterval,
    fileURLToPath: () => path.join(root, 'im', 'recruiter-na.mjs'),
    process: { pid: 1, on() {}, env: { CODEX_HOME: root, DINGTALK_CLIENT_ID: 'synthetic',
      DINGTALK_CLIENT_SECRET: 'synthetic', RECRUITER_RESUME_URL_BASE: 'https://unsafe.example/resumes',
      RECRUITER_RESUME_URL_DIR: path.join(root, 'public') }, stderr: { write() {} } },
    console: { log() {}, error: (...args) => logs.push(args.join(' ')) },
    CodexCliRuntime: class {}, UnifiedImBridge: class {},
    FileDedupeStore: class { claim() { return true; } },
    DingTalkStreamBotAdapter: class { async start(fn) { handler = fn; } },
    handleControlCommand: async () => false, shouldUseHiringWorkflow: () => true,
    spawn() {
      const child = new EventEmitter();
      child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
      queueMicrotask(() => { child.stdout.emit('data', JSON.stringify(result)); child.emit('close', 0); });
      return child;
    },
  };
  await vm.runInNewContext(`(async () => {${source}\n})()`, context);
  return { handler, logs };
}

for (const mode of ['success', 'missing', 'throws', 'false', 'business-failure', 'empty', 'malformed', 'success-flag']) {
  test(`selected resume ${mode}: no public copy, truthful message, safe logs`, async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rn-synthetic-'));
    try {
      const file = path.join(root, 'synthetic.pdf');
      fs.writeFileSync(file, 'synthetic resume');
      const { handler, logs } = await boot(root, { handled: true, state: 'SELECTED', message: '已选中。',
        resume_delivery: { file_path: file, filename: 'synthetic.pdf' } });
      const messages = [];
      let calls = 0;
      const responder = { reply: async text => messages.push(text) };
      if (mode !== 'missing') responder.sendFile = async request => {
        calls++;
        assert.equal(request.filePath, file);
        if (mode === 'throws') throw new Error(`${file} secret-token`);
        return { success: { errcode: 0 }, false: false, 'business-failure': { errcode: 400, errmsg: 'secret-token' },
          empty: undefined, malformed: {}, 'success-flag': { ok: true } }[mode];
      };
      for (let i = 0; i < 2; i++) await handler({ senderId: 'synthetic-owner', messageId: `synthetic-${i}`, text: '选中' }, responder);
      assert.equal(calls, mode === 'missing' ? 0 : 2);
      assert.equal(fs.existsSync(path.join(root, 'public')), false);
      assert.equal(messages.length, 2);
      for (const message of messages) {
        assert.equal(message.includes('已提交钉钉文件投递'), mode === 'success');
        if (mode !== 'success') assert.match(message, /暂未送达/);
        assert.doesNotMatch(message, /https:|secret-token|synthetic\.pdf/);
      }
      assert.equal(logs.join('\n').includes(file), false);
      assert.equal(logs.join('\n').includes('secret-token'), false);
    } finally { fs.rmSync(root, { recursive: true, force: true }); }
  });
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { handleControlCommand, shouldUseHiringWorkflow } from './router.mjs';

test('ordinary messages are routed to the hiring-manager workflow', () => {
  assert.equal(shouldUseHiringWorkflow('我要招一个医学联络官'), true);
  assert.equal(shouldUseHiringWorkflow('岗位画像库有多少个？'), true);
});

test('/new is handled locally and exactly once', async () => {
  const replies = [];
  const handled = await handleControlCommand({ text: '/new', messageId: 'm1' }, { reply: async x => replies.push(x) });
  assert.equal(handled, true);
  assert.deepEqual(replies, ['已开启新会话，请发送你的问题。']);
});

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
  assert.deepEqual(replies, ['请直接发送“我要招一个……”创建新招聘需求；已有需求不会因 /new 被清空。']);
});

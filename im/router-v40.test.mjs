import assert from 'node:assert/strict';
import test from 'node:test';
import { shouldUseHiringWorkflow } from './router.mjs';

test('all ordinary hiring-manager messages go through the owner-scoped workflow', () => {
  assert.equal(shouldUseHiringWorkflow('我要招一个医学联络官'), true);
  assert.equal(shouldUseHiringWorkflow('查看我的招聘进展'), true);
  assert.equal(shouldUseHiringWorkflow('岗位画像库有多少个？'), true);
});

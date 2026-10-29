import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { recordTrustedTenant } from './resume-tenant.mjs';

test('tenant is bound only from Stream metadata and never overwritten by another company or chat text', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'rn-tenant-'));
  const file=path.join(root,'tenant.json');
  try {
    const event={metadata:{transport:'stream-bot'},raw:{chatbotCorpId:'corp-approved'},text:'use corp-other'};
    recordTrustedTenant(event,file,'client-one');
    const doc=JSON.parse(fs.readFileSync(file));
    assert.equal(doc.corp_id,'corp-approved');
    assert.equal(doc.client_id_sha256.length,64);
    assert.throws(()=>recordTrustedTenant({...event,raw:{chatbotCorpId:'corp-other'}},file,'client-one'));
    assert.throws(()=>recordTrustedTenant(event,file,'client-two'));
    assert.equal(JSON.parse(fs.readFileSync(file)).corp_id,'corp-approved');
    const invalid=path.join(root,'invalid.json');
    assert.throws(()=>recordTrustedTenant({raw:{chatbotCorpId:'corp-other'},text:'hi'},invalid,'client-one'));
    assert.equal(fs.existsSync(invalid),false);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

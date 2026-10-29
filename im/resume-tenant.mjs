// Bind the download audience to the bot's tenant from authenticated Stream metadata.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export function recordTrustedTenant(envelope, filename, clientId) {
  const corpId = envelope?.raw?.chatbotCorpId;
  if (envelope?.metadata?.transport !== 'stream-bot' || !clientId ||
      typeof corpId !== 'string' || !/^[A-Za-z0-9_-]{3,128}$/.test(corpId)) {
    throw new Error('DOWNLOAD_TENANT_NOT_VERIFIED');
  }
  const clientHash = createHash('sha256').update(clientId).digest('hex');
  fs.mkdirSync(path.dirname(filename), {recursive:true, mode:0o700});
  try {
    fs.writeFileSync(filename, JSON.stringify({schema_version:1,corp_id:corpId,client_id_sha256:clientHash}), {flag:'wx',mode:0o600});
  } catch(error) {
    if (error.code !== 'EEXIST') throw new Error('DOWNLOAD_TENANT_STORE_UNAVAILABLE');
  }
  const stored = JSON.parse(fs.readFileSync(filename,'utf8'));
  if (stored.corp_id !== corpId || stored.client_id_sha256 !== clientHash) {
    throw new Error('DOWNLOAD_TENANT_MISMATCH');
  }
}

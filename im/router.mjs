/** Recruiter-Na v5 hiring-manager routing. */

export async function handleControlCommand(envelope, responder = {}) {
  const text = String(envelope?.text || '').trim();
  if (text !== '/new') return false;
  console.log(`[router] /new msg=${envelope.messageId}`);
  await responder.reply?.('请直接发送“我要招一个……”创建新招聘需求；已有需求不会因 /new 被清空。');
  console.log(`[reply] source=router msg=${envelope.messageId}`);
  return true;
}

/**
 * v5 deliberately sends every ordinary hiring-manager message through the
 * owner-scoped workflow. Public profile/JD/resume browsing routes were removed.
 */
export function shouldUseHiringWorkflow(text) {
  return Boolean(String(text || '').trim());
}

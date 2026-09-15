/** Recruiter-Na v5 hiring-manager routing. */

export async function handleControlCommand(envelope, responder = {}) {
  const text = String(envelope?.text || '').trim();
  if (text !== '/new') return false;
  console.log(`[router] /new msg=${envelope.messageId}`);
  await responder.reply?.('已开启新会话，请发送你的问题。');
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

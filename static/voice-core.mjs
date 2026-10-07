// HB-012 safety helpers. Recognition rule follows the tested Stage-1 prototype.
// Runtime wiring, provider access and physical acceptance are separate gates.
export const COMPLETION = 'Roger out';
const FINISH = /\broger(?:[\s,;:!?.…-]+)out(?:[\s,;:!?.…-]*)$/iu;
export function detectCompletion(transcript) {
  const text = String(transcript || '');
  const match = text.match(FINISH);
  return match ? {phrase: COMPLETION, requestText: text.slice(0, match.index).trim()} : null;
}
export function finalizedRequest(transcript) {
  const result = detectCompletion(transcript);
  if (!result || !result.requestText) throw new Error('A final request ending in Roger out is required');
  if (result.requestText.length > 8000) throw new Error('The request exceeds 8000 characters');
  return result.requestText;
}
export class FinalTurn {
  constructor({generation, threadId, draftRevision, requestId}) {
    if (!Number.isSafeInteger(generation) || generation < 0 ||
        !Number.isSafeInteger(draftRevision) || draftRevision < 0 ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(threadId) ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(requestId)) {
      throw new Error('Invalid frozen turn identity');
    }
    this.identity = Object.freeze({generation, threadId, draftRevision, requestId});
    this.phase = 'capturing'; this.itemId = null; this.contentIndex = null;
    this.payload = null;
  }
  commit({generation, itemId, contentIndex}) {
    if (generation !== this.identity.generation || this.phase !== 'capturing') return false;
    if (typeof itemId !== 'string' || !itemId || !Number.isSafeInteger(contentIndex) || contentIndex < 0) {
      throw new Error('Commit requires the exact transcript item identity');
    }
    this.itemId = itemId; this.contentIndex = contentIndex; this.phase = 'finalizing';
    return true;
  }
  complete({generation, itemId, contentIndex, transcript}) {
    if (generation !== this.identity.generation || this.phase !== 'finalizing' ||
        itemId !== this.itemId || contentIndex !== this.contentIndex) return null;
    const text = finalizedRequest(transcript);
    this.payload = Object.freeze({request_id: this.identity.requestId,
      thread_id: this.identity.threadId, draft_revision: this.identity.draftRevision,
      source: 'voice', text});
    this.phase = 'pending'; return this.payload;
  }
  accept(requestId) {
    if (requestId !== this.identity.requestId || this.phase !== 'pending') return false;
    this.phase = 'accepted'; return true;
  }
  cancel() {
    if (this.phase === 'accepted') return false;
    this.phase = 'cancelled'; return true;
  }
}

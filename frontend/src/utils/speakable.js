/**
 * Speakable text — turn an assistant reply (Markdown, emoji, links) into
 * plain sentences for speechSynthesis.
 *
 * Without this, read-aloud literally says "asterisk asterisk" for **bold**
 * and reads every emoji's Unicode name ("smiling face", "mobile phone").
 * This strips the formatting and drops emoji, so the voice reads the
 * *message* instead of the *markup*.
 *
 * Pure functions, no dependencies — safe to unit-test in Node too.
 */

// Emoji / pictographs: match the Unicode pictographic ranges plus the
// modifiers, variation selectors and joiners that glue sequences together.
const EMOJI_RE =
  /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE00}-\u{FE0F}\u{200D}\u{2190}-\u{21FF}\u{2300}-\u{23FF}\u{2C00}-\u{2C5F}]/gu;

// Urdu / Arabic script — used to pick an Urdu TTS voice + recognition lang.
const URDU_SCRIPT_RE = /[\u0600-\u06FF]/;

export function containsUrduScript(text) {
  return URDU_SCRIPT_RE.test(String(text || ''));
}

/**
 * Guess the reply language for voice selection: 'ur' when the text carries
 * Urdu/Arabic script, otherwise 'en'. (Roman Urdu is still read with the
 * default voice — browsers have no Roman-Urdu voice.)
 */
export function detectSpeechLang(text) {
  return containsUrduScript(text) ? 'ur' : 'en';
}

/**
 * Strip Markdown + emoji + URLs down to speakable plain text.
 */
export function toSpeakableText(input) {
  let text = String(input || '');

  // Fenced code blocks → keep a short placeholder (reading code aloud is noise).
  text = text.replace(/```[\s\S]*?```/g, '. code omitted. ');
  // Inline code → keep the words, drop the backticks.
  text = text.replace(/`([^`]*)`/g, '$1');
  // Images ![alt](url) → alt text only.
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
  // Links [label](url) → label only.
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
  // Bold / italic / strikethrough markers → plain words.
  text = text.replace(/(\*\*|__)(.*?)\1/g, '$2');
  text = text.replace(/(\*|_)([^*_]+)\1/g, '$2');
  text = text.replace(/~~(.*?)~~/g, '$1');
  // Headings "# Title" → "Title.".
  text = text.replace(/^\s{0,3}#{1,6}\s+(.+)$/gm, '$1. ');
  // Blockquotes "> …" → the words.
  text = text.replace(/^\s{0,3}>\s?/gm, '');
  // Horizontal rules.
  text = text.replace(/^\s{0,3}([-*_])(\s*\1){2,}\s*$/gm, '. ');
  // Tables: pipes become pauses, separator rows vanish.
  text = text.replace(/^\s*\|?[\s:|-]+\|[\s:|.-]*$/gm, ' ');
  text = text.replace(/\|/g, ', ');
  // Bullets / numbered lists → sentence pauses.
  text = text.replace(/^\s*[-*+•]\s+/gm, '. ');
  text = text.replace(/^\s*\d+[.)]\s+/gm, '. ');
  // Mid-line " - " list separators → pauses (line-start case handled above).
  text = text.replace(/\s+-\s+/g, '. ');
  // Bare URLs → "link" (reading a full URL aloud is useless).
  text = text.replace(/https?:\/\/[^\s)]+/g, 'link');
  // Email addresses → "email address".
  text = text.replace(/[\w.+-]+@[\w-]+\.[\w.]+/g, 'email address');
  // Emoji → drop entirely (the bug: voices read their Unicode names).
  text = text.replace(EMOJI_RE, ' ');
  // Leftover markdown punctuation that voices would spell out.
  text = text.replace(/[*_#`~]/g, '');
  // Normalise whitespace, then tidy doubled punctuation.
  text = text.replace(/[ \t]+/g, ' ');
  text = text.replace(/\n{3,}/g, '\n\n');
  text = text.replace(/([.!?,;:])\1+/g, '$1');
  text = text.replace(/\s+([.,!?;:])/g, '$1');
  text = text.trim();

  return text;
}

/**
 * Pick the best speechSynthesis voice for `lang` ('ur' | 'en').
 * Prefers an exact ur-PK/ur voice for Urdu, else any default English voice.
 * Returns null when nothing matches — the caller then uses the browser default.
 */
export function pickVoice(voices, lang) {
  const list = Array.isArray(voices) ? voices : [];
  if (!list.length) return null;
  const lower = (v) => `${v?.lang || ''} ${v?.name || ''}`.toLowerCase();

  if (lang === 'ur') {
    // Exact Urdu voices first (Chrome ships "Urdu Pakistan" on many systems).
    const exact = list.find((v) => (v.lang || '').toLowerCase().startsWith('ur'));
    if (exact) return exact;
    // Fallback: Arabic-script-capable voices still pronounce Urdu better
    // than an English voice. Hindi shares much of the phonetics too.
    const near =
      list.find((v) => lower(v).includes('arabic')) || list.find((v) => lower(v).includes('hindi'));
    if (near) return near;
    return null;
  }

  const exactEn =
    list.find((v) => v.default && (v.lang || '').toLowerCase().startsWith('en')) ||
    list.find((v) => (v.lang || '').toLowerCase() === 'en-us') ||
    list.find((v) => (v.lang || '').toLowerCase().startsWith('en'));
  return exactEn || null;
}

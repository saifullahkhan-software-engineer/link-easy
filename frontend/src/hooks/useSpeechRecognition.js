import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Mic input via the browser's Web Speech API.
 *
 * Chrome, Edge and Safari ship (prefixed) SpeechRecognition; Firefox does
 * not — `supported` is false there and the mic button stays hidden, so the
 * text input is always the fallback. Recognition runs entirely on-device in
 * the browser; nothing is recorded or uploaded by this app.
 *
 * Returns:
 *   supported  — the browser exposes SpeechRecognition
 *   listening  — recognition is active
 *   transcript — final text recognised so far (reset on start)
 *   error      — human-readable one-liner ("no-speech", "mic denied", …)
 *   start() / stop()
 */
export default function useSpeechRecognition({ onFinal } = {}) {
  const Recognition =
    typeof window !== 'undefined'
      ? window.SpeechRecognition || window.webkitSpeechRecognition
      : null;
  const supported = Boolean(Recognition);

  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState('');
  const recognitionRef = useRef(null);
  const onFinalRef = useRef(onFinal);
  // Deliberately no language forcing: recognition follows the user's browser
  // locale, so Urdu/English mixing works as well as their keyboard does.
  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  const stop = useCallback(() => {
    try {
      recognitionRef.current?.stop();
    } catch {
      /* already stopped */
    }
  }, []);

  const start = useCallback(() => {
    if (!supported || recognitionRef.current) return;
    setError('');
    setTranscript('');

    const recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = recognition.lang || undefined;

    recognition.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) final += result[0].transcript;
        else interim += result[0].transcript;
      }
      setTranscript((final || interim).trim());
      if (final.trim() && onFinalRef.current) onFinalRef.current(final.trim());
    };
    recognition.onerror = (event) => {
      const code = event?.error || 'unknown';
      setError(
        code === 'not-allowed'
          ? 'Microphone access was blocked — allow it in the browser bar.'
          : code === 'no-speech'
            ? "Didn't catch that — try again."
            : 'Voice input hit an error. Type instead?'
      );
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
    };

    recognitionRef.current = recognition;
    setListening(true);
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setListening(false);
    }
  }, [Recognition, supported]);

  useEffect(() => () => stop(), [stop]);

  return { supported, listening, transcript, error, start, stop };
}

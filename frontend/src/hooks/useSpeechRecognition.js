import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Mic input via the browser's Web Speech API — continuous, hands-free style.
 *
 * Chrome, Edge and Safari ship (prefixed) SpeechRecognition; Firefox does
 * not — `supported` is false there and the mic button stays hidden, so the
 * text input is always the fallback. Recognition runs in the browser;
 * nothing is recorded or uploaded by this app.
 *
 * Behaviour (what "mic stays open" means):
 * - `start()` begins listening and KEEPS listening until `stop()` is called.
 * - While the user talks, interim words stream into `transcript`.
 * - When the user goes quiet for `SILENCE_MS` (~1.5s), the captured command
 *   fires through `onFinal` (the widget sends it) and the mic immediately
 *   keeps listening for the next command — no tap needed between commands.
 * - The browser still ends the underlying session on its own sometimes
 *   (pauses, timeouts); the hook silently restarts it while enabled.
 * - `paused` (set by the widget while the reply is read aloud) freezes
 *   capture so the mic doesn't transcribe the assistant's own voice, then
 *   resumes automatically.
 *
 * Languages: `lang` is a BCP-47 tag or 'auto' (browser default). Urdu users
 * pick 'ur-PK' from the widget toggle; English 'en-US'. Changing it while
 * listening restarts the session with the new language.
 *
 * Returns: supported, listening, paused, transcript (live words), error,
 * lang, setLang, start(), stop().
 */

export const RECOGNITION_LANGS = [
  { id: 'auto', label: 'Auto', hint: 'Follow the browser language' },
  { id: 'en-US', label: 'EN', hint: 'English' },
  { id: 'ur-PK', label: 'اردو', hint: 'Urdu' },
];

const SILENCE_MS = 1500; // quiet gap that ends one voice command
const RESTART_MS = 250; // delay before reviving a browser-ended session

function resolveRecognition() {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export default function useSpeechRecognition({ onFinal, onSpeechStart } = {}) {
  const Recognition = resolveRecognition();
  const supported = Boolean(Recognition);

  const [listening, setListening] = useState(false);
  const [paused, setPaused] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState('');
  const [lang, setLangState] = useState('auto');

  const recognitionRef = useRef(null);
  const enabledRef = useRef(false); // the user wants the mic open
  const pausedRef = useRef(false); // frozen while TTS reads the reply
  const langRef = useRef('auto');
  const finalBufferRef = useRef(''); // completed phrases of the current command
  const silenceTimerRef = useRef(null);
  const restartTimerRef = useRef(null);
  const onFinalRef = useRef(onFinal);
  const onSpeechStartRef = useRef(onSpeechStart);

  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);

  useEffect(() => {
    onSpeechStartRef.current = onSpeechStart;
  }, [onSpeechStart]);

  const clearSilenceTimer = () => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
  };

  const clearRestartTimer = () => {
    if (restartTimerRef.current) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  };

  // Fire the buffered command (if any) and reset for the next one — the
  // session itself keeps running.
  const flushCommand = useCallback(() => {
    clearSilenceTimer();
    const command = finalBufferRef.current.trim();
    finalBufferRef.current = '';
    setTranscript('');
    if (command && onFinalRef.current) onFinalRef.current(command);
  }, []);

  const armSilenceTimer = useCallback(() => {
    clearSilenceTimer();
    silenceTimerRef.current = setTimeout(() => {
      // 1–2s of quiet = the command is finished → execute it, mic stays open.
      flushCommand();
    }, SILENCE_MS);
  }, [flushCommand]);

  const teardownRecognition = useCallback(() => {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition.onspeechstart = null;
      recognition.onsoundstart = null;
      try {
        recognition.stop();
      } catch {
        /* already stopped */
      }
    }
  }, []);

  const launch = useCallback(() => {
    if (!supported || recognitionRef.current || !enabledRef.current) return;
    setError('');

    const recognition = new Recognition();
    recognition.continuous = true; // don't auto-stop after one utterance
    recognition.interimResults = true;
    const wanted = langRef.current;
    if (wanted && wanted !== 'auto') recognition.lang = wanted;
    // 'auto' leaves recognition.lang at the browser default, which follows
    // the user's locale — English/Urdu mixing works there too.

    recognition.onspeechstart = () => {
      onSpeechStartRef.current?.();
    };

    recognition.onsoundstart = () => {
      onSpeechStartRef.current?.();
    };

    recognition.onresult = (event) => {
      onSpeechStartRef.current?.();
      if (pausedRef.current) return; // ignore our own read-aloud
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const text = result[0]?.transcript || '';
        if (result.isFinal) finalBufferRef.current += `${text} `;
        else interim += text;
      }
      const live = `${finalBufferRef.current}${interim}`.trim();
      setTranscript(live);
      // Every chunk of speech restarts the quiet-countdown.
      if (live) armSilenceTimer();
    };

    recognition.onerror = (event) => {
      const code = event?.error || 'unknown';
      if (code === 'no-speech' || code === 'aborted') return; // quiet room / restart — not an error
      if (code === 'not-allowed') {
        setError('Microphone access was blocked — allow it in the browser bar.');
        enabledRef.current = false;
        setListening(false);
        return;
      }
      // 'network', 'audio-capture', … — surface once, keep the mic open so a
      // transient blip doesn't kill hands-free mode.
      setError('Voice input hit an error. Still listening — or type instead?');
    };

    recognition.onend = () => {
      recognitionRef.current = null;
      clearSilenceTimer();
      if (!enabledRef.current) {
        setListening(false);
        return;
      }
      // The browser ended the session (pause timeout, background tab, …)
      // while the user still wants the mic open: flush any partial command
      // only if it looks complete, then revive the session.
      if (finalBufferRef.current.trim()) flushCommand();
      clearRestartTimer();
      restartTimerRef.current = setTimeout(() => {
        if (enabledRef.current && !recognitionRef.current) {
          setListening(true);
          launch();
        }
      }, RESTART_MS);
    };

    recognitionRef.current = recognition;
    setListening(true);
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setListening(false);
    }
  }, [Recognition, supported, armSilenceTimer, flushCommand]);

  const start = useCallback(() => {
    if (!supported) return;
    clearRestartTimer();
    enabledRef.current = true;
    pausedRef.current = false;
    setPaused(false);
    setTranscript('');
    finalBufferRef.current = '';
    launch();
  }, [supported, launch]);

  const stop = useCallback(() => {
    enabledRef.current = false;
    pausedRef.current = false;
    setPaused(false);
    clearSilenceTimer();
    clearRestartTimer();
    finalBufferRef.current = '';
    setTranscript('');
    teardownRecognition();
    setListening(false);
  }, [teardownRecognition]);

  // Freeze capture while the assistant reads its reply aloud (so the mic
  // doesn't hear the speaker), then resume — the mic still looks "open".
  const setPausedFlag = useCallback(
    (value) => {
      if (!enabledRef.current) return;
      pausedRef.current = value;
      setPaused(value);
      if (value) {
        clearSilenceTimer();
        try {
          recognitionRef.current?.stop(); // onend revives it (paused → ignored)
        } catch {
          /* noop */
        }
      } else {
        setTranscript('');
        finalBufferRef.current = '';
        if (!recognitionRef.current) launch();
      }
    },
    [launch]
  );

  const setLang = useCallback(
    (next) => {
      const id = RECOGNITION_LANGS.some((l) => l.id === next) ? next : 'auto';
      langRef.current = id;
      setLangState(id);
      // A live session keeps its old language — restart it so Urdu/English
      // switches apply immediately.
      if (enabledRef.current) {
        clearRestartTimer();
        teardownRecognition();
        launch();
      }
    },
    [launch, teardownRecognition]
  );

  useEffect(() => () => stop(), [stop]);

  return {
    supported,
    listening,
    paused,
    transcript,
    error,
    lang,
    setLang,
    start,
    stop,
    setPaused: setPausedFlag,
  };
}

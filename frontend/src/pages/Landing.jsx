import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useAdminAccess } from '../hooks/useAdminAccess';

const coreFeatures = [
  {
    title: 'Connect your social tools',
    body: 'Bring LinkedIn, WhatsApp, Gmail, YouTube Shorts, Instagram Reels, TikTok, and Facebook into one focused workspace without changing how your accounts are used.',
    icon: 'connect',
    tag: 'Universal Sync',
  },
  {
    title: 'Build campaigns in minutes',
    body: 'Turn a lead list into a clear sequence of profile visits, connection requests, and personal follow-ups with human-like randomized delays.',
    icon: 'campaign',
    tag: 'Lead Sequences',
  },
  {
    title: 'Automate the daily busywork',
    body: 'Keep short-form video publishing, outreach sequences, and message scanning moving with schedules, sensible limits, and background workers.',
    icon: 'automate',
    tag: 'Autonomous Queue',
  },
  {
    title: 'Take over when it matters',
    body: 'Open live conversations on LinkedIn and WhatsApp or check your Gmail inbox, read what came in, and respond yourself whenever a real human touch is needed.',
    icon: 'chat',
    tag: 'Live Takeover',
  },
  {
    title: 'AI-Powered Copy & Hooks',
    body: 'Automatically convert rough video scripts or notes into high-converting titles, viral caption hooks, and targeted hashtags per platform using Groq LLM.',
    icon: 'sparkles',
    tag: 'Groq AI Engine',
  },
  {
    title: 'Visual Calendar & Queue Control',
    body: 'Plan your multi-channel publishing schedule on an interactive monthly calendar. Monitor live execution and re-queue any failed post in one click.',
    icon: 'calendar',
    tag: 'Content Scheduler',
  },
];

const socialAutomationFeatures = [
  {
    title: 'Multi-Platform Video Scheduler',
    description: 'Upload once to publish high-performing vertical videos directly to YouTube Shorts, Instagram Reels, TikTok, and Facebook Reels simultaneously.',
    icon: 'video',
    badge: '4 Platforms at Once',
    bullets: [
      'Direct API video upload with zero watermarks',
      'Select YouTube Playlists automatically per post',
      'Toggle Instagram feed sharing & Reel placement',
      'Configurable privacy, stitch, and duet settings for TikTok',
    ],
  },
  {
    title: 'In-Browser Video Studio & Frame Picker',
    description: 'Trim your clips and pick custom thumbnail covers directly in LinkEasy before scheduling—no external video editing software required.',
    icon: 'scissors',
    badge: 'FFmpeg Inside',
    bullets: [
      'Server-side FFmpeg precision clipping and re-encoding',
      'Interactive timestamp scrubber for instant trimming',
      'Frame-accurate thumbnail extractor from any video second',
      'Custom image cover upload with live preview',
    ],
  },
  {
    title: 'AI Copy & Viral Hook Generator',
    description: 'Groq-powered copy intelligence extracts platform-tailored headlines, hooks, descriptions, and hashtags from any pasted text or script.',
    icon: 'sparkles',
    badge: 'Groq LLM Powered',
    bullets: [
      'YouTube-optimized titles and detailed descriptions',
      'Attention-grabbing first-line hooks for Instagram Reels',
      'Punchy captions and trending hashtags for TikTok',
      'Automatic parsing with robust offline fallback',
    ],
  },
  {
    title: 'Interactive Queue & Smart Recovery',
    description: 'A resilient Celery/Redis scheduling pipeline that monitors every post status and recovers from network rate limits gracefully.',
    icon: 'queue',
    badge: 'Redis & Celery Engine',
    bullets: [
      'Live status tracking: Pending, Publishing, Published, Failed',
      'One-click re-queue with custom time rescheduling',
      'Facebook Groups distribution checklist with 1-click copy',
      'Actionable error diagnostics with clear failure reasons',
    ],
  },
];

const steps = [
  {
    number: '01',
    title: 'Connect',
    tagline: 'Link your accounts in seconds',
    body: 'Securely connect the LinkedIn, WhatsApp, Gmail, YouTube, Instagram, TikTok, and Facebook accounts you already use with official OAuth and durable sessions.',
  },
  {
    number: '02',
    title: 'Create',
    tagline: 'Upload content or configure outreach',
    body: 'Build an outreach campaign, set up a WhatsApp keyword monitor, or upload a video with AI-generated titles, hooks, and hashtags.',
  },
  {
    number: '03',
    title: 'Automate',
    tagline: 'Background workers do the heavy lifting',
    body: 'Set your schedule once and let LinkEasy handle the repeatable work each day with human-like pacing, automatic retries, and unified analytics.',
  },
];

const platforms = [
  {
    id: 'youtube',
    name: 'YouTube Shorts',
    badge: 'Ready',
    description: 'Direct video upload, playlist integration & privacy management.',
    color: 'text-red-400 border-red-500/20 bg-red-500/5',
    iconColor: 'text-red-400',
  },
  {
    id: 'instagram',
    name: 'Instagram Reels',
    badge: 'Ready',
    description: 'Container-based publishing, custom cover frames & feed toggle.',
    color: 'text-pink-400 border-pink-500/20 bg-pink-500/5',
    iconColor: 'text-pink-400',
  },
  {
    id: 'tiktok',
    name: 'TikTok',
    badge: 'Ready',
    description: 'Creator direct publishing, custom privacy & interaction settings.',
    color: 'text-cyan-400 border-cyan-500/20 bg-cyan-500/5',
    iconColor: 'text-cyan-400',
  },
  {
    id: 'facebook',
    name: 'Facebook Reels & Groups',
    badge: 'Ready',
    description: 'Page Reels video publishing and manual Group sharing checklists.',
    color: 'text-blue-400 border-blue-500/20 bg-blue-500/5',
    iconColor: 'text-blue-400',
  },
  {
    id: 'linkedin',
    name: 'LinkedIn',
    badge: 'Ready',
    description: 'Profile visits, personalized connection requests, follow-ups & live chat.',
    color: 'text-sky-400 border-sky-500/20 bg-sky-500/5',
    iconColor: 'text-sky-400',
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp',
    badge: 'Ready',
    description: 'Group message keyword scanner, AI relevance scoring & live chat.',
    color: 'text-emerald-400 border-emerald-500/20 bg-emerald-500/5',
    iconColor: 'text-emerald-400',
  },
  {
    id: 'gmail',
    name: 'Gmail',
    badge: 'Ready',
    description: 'Connect your mailbox: check & read email, manage labels, reply in one place.',
    color: 'text-rose-400 border-rose-500/20 bg-rose-500/5',
    iconColor: 'text-rose-400',
  },
];

function LinkedInMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M5.2 7.35H1.35V22H5.2V7.35ZM3.28 1A2.25 2.25 0 1 0 3.3 5.5 2.25 2.25 0 0 0 3.28 1ZM22.65 13.6c0-4.42-2.36-6.47-5.5-6.47a4.75 4.75 0 0 0-4.3 2.37h-.06V7.35H9.1V22h3.85v-7.25c0-1.91.36-3.76 2.73-3.76 2.34 0 2.37 2.19 2.37 3.88V22h3.85l.75-8.4Z" />
    </svg>
  );
}

function WhatsAppMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.5 3.47A11.8 11.8 0 0 0 1.94 17.7L.27 23.8l6.24-1.63A11.78 11.78 0 0 0 12.15 23h.01A11.84 11.84 0 0 0 24 11.19c0-3.15-1.24-6.12-3.5-7.72Zm-8.34 17.54h-.01a9.8 9.8 0 0 1-5-1.37l-.36-.21-3.7.97.99-3.61-.24-.37A9.82 9.82 0 1 1 12.16 21Zm5.39-7.36c-.3-.15-1.75-.86-2.02-.96-.27-.1-.47-.15-.67.15-.2.3-.76.96-.93 1.16-.17.2-.34.22-.64.07-.3-.15-1.25-.46-2.38-1.47a8.9 8.9 0 0 1-1.65-2.06c-.17-.3-.02-.46.13-.6.13-.13.3-.34.44-.52.15-.17.2-.3.3-.49.1-.2.05-.37-.03-.52-.07-.15-.66-1.6-.91-2.19-.24-.57-.49-.49-.67-.5h-.57c-.2 0-.52.07-.79.37-.27.3-1.04 1.01-1.04 2.46 0 1.45 1.06 2.86 1.21 3.05.15.2 2.08 3.18 5.04 4.46.7.3 1.25.49 1.68.62.71.22 1.35.19 1.86.12.57-.09 1.75-.72 2-1.41.25-.7.25-1.3.17-1.42-.07-.12-.27-.2-.56-.35Z" />
    </svg>
  );
}

function YouTubeMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-5.8ZM9.6 15.6V8.4l6.2 3.6-6.2 3.6Z" />
    </svg>
  );
}

function InstagramMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

function TikTokMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M16.6 5.8A4.3 4.3 0 0 1 15.5 3h-3.1v12.4a2.6 2.6 0 1 1-2.6-2.6c.3 0 .5 0 .8.1V9.7a5.7 5.7 0 1 0 4.9 5.7V9.1a7.3 7.3 0 0 0 4.3 1.4V7.4a4.3 4.3 0 0 1-3.2-1.6Z" />
    </svg>
  );
}

function FacebookMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M13.6 21v-8h2.7l.4-3h-3.1V8.1c0-.9.3-1.6 1.7-1.6h1.8V3.8c-.3 0-1.3-.1-2.4-.1-2.4 0-4 1.5-4 4.1V10H8v3h2.7v8h2.9Z" />
    </svg>
  );
}

function GmailMark({ className = 'h-5 w-5' }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M1.5 5.25A2.25 2.25 0 0 1 3.75 3h16.5a2.25 2.25 0 0 1 2.25 2.25v13.5A2.25 2.25 0 0 1 20.25 21H3.75a2.25 2.25 0 0 1-2.25-2.25V5.25Zm1.5.66v12.84c0 .41.34.75.75.75h16.5c.41 0 .75-.34.75-.75V5.91l-8.28 6.07a1.5 1.5 0 0 1-1.68 0L3 5.91Zm1.03-.66L12 11.32l7.97-6.07H4.03Z" />
    </svg>
  );
}

function PlatformIcon({ id, className = 'h-5 w-5' }) {
  switch (id) {
    case 'youtube':
      return <YouTubeMark className={className} />;
    case 'instagram':
      return <InstagramMark className={className} />;
    case 'tiktok':
      return <TikTokMark className={className} />;
    case 'facebook':
      return <FacebookMark className={className} />;
    case 'linkedin':
      return <LinkedInMark className={className} />;
    case 'whatsapp':
      return <WhatsAppMark className={className} />;
    case 'gmail':
      return <GmailMark className={className} />;
    default:
      return null;
  }
}

function FeatureIcon({ name }) {
  const paths = {
    connect: (
      <>
        <path d="M8 8.5h8M8 15.5h8" />
        <path d="M6 5.5A2.5 2.5 0 1 1 1 5.5a2.5 2.5 0 0 1 5 0ZM23 5.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM6 18.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0ZM23 18.5a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z" />
      </>
    ),
    campaign: (
      <>
        <path d="M5 3.5h11l3 3v14H5z" />
        <path d="M8.5 9h7M8.5 13h7M8.5 17h4" />
      </>
    ),
    automate: (
      <>
        <path d="M12 2.5v3M12 18.5v3M21.5 12h-3M5.5 12h-3M18.72 5.28 16.6 7.4M7.4 16.6l-2.12 2.12M18.72 18.72 16.6 16.6M7.4 7.4 5.28 5.28" />
        <circle cx="12" cy="12" r="4.25" />
        <path d="m10.5 12 1 1 2.25-2.4" />
      </>
    ),
    chat: (
      <>
        <path d="M4 4h16v12H9l-5 4V4Z" />
        <path d="M8 9h8M8 12.5h5" />
      </>
    ),
    sparkles: (
      <>
        <path d="m12 3 1.912 5.885L19.8 10.8 13.912 12.715 12 18.6l-1.912-5.885L4.2 10.8l5.888-1.915L12 3Z" />
        <path d="M19 16l.9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9L19 16Z" />
      </>
    ),
    calendar: (
      <>
        <rect width="18" height="18" x="3" y="4" rx="2" />
        <path d="M16 2v4M8 2v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01" />
      </>
    ),
    video: (
      <>
        <polygon points="23 7 16 12 23 17 23 7" />
        <rect width="14" height="14" x="2" y="5" rx="2" />
      </>
    ),
    scissors: (
      <>
        <circle cx="6" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <line x1="20" x2="8.12" y1="4" y2="15.88" />
        <line x1="14.47" x2="20" y1="14.48" y2="20" />
        <line x1="8.12" x2="12" y1="8.12" y2="12" />
      </>
    ),
    queue: (
      <>
        <line x1="18" x2="6" y1="6" y2="6" />
        <line x1="21" x2="3" y1="12" y2="12" />
        <line x1="18" x2="6" y1="18" y2="18" />
      </>
    ),
  };
  return (
    <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="m4 10 3.5 3.5L16 5.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M4 10h12m-4.5-4.5L16 10l-4.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Comprehensive, product-accurate hero preview showing both social video automation & multi-channel outreach. */
function AutomationPreview() {
  const [activeTab, setActiveTab] = useState('social');

  return (
    <div className="relative mx-auto w-full max-w-[620px] lg:ml-auto">
      {/* Background glow effects */}
      <div className="absolute -inset-8 rounded-full bg-accent-500/15 blur-3xl" />
      <div className="absolute -bottom-10 -right-10 h-64 w-64 rounded-full bg-indigo-500/10 blur-3xl" />

      {/* Main glass card */}
      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-surface-900/95 shadow-2xl shadow-black/60 backdrop-blur-xl">
        {/* Window title bar */}
        <div className="flex items-center justify-between border-b border-surface-700/80 px-4 py-3 sm:px-5">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-red-400/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/80" />
            <span className="ml-2 text-xs font-medium text-zinc-400">LinkEasy Automation Engine</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full bg-accent-500/10 px-2 py-0.5 text-[10px] font-medium text-accent-300 ring-1 ring-inset ring-accent-500/20">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-400" />
              6 Channels Live
            </span>
          </div>
        </div>

        {/* Tab switcher inside mockup */}
        <div className="flex border-b border-surface-800 bg-surface-950/60 px-4 pt-2 sm:px-5">
          <button
            type="button"
            onClick={() => setActiveTab('social')}
            className={`border-b-2 px-3 py-2 text-xs font-semibold transition ${
              activeTab === 'social'
                ? 'border-accent-400 text-accent-300'
                : 'border-transparent text-zinc-400 hover:text-zinc-200'
            }`}
          >
            Social Video Scheduler
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('outreach')}
            className={`border-b-2 px-3 py-2 text-xs font-semibold transition ${
              activeTab === 'outreach'
                ? 'border-accent-400 text-accent-300'
                : 'border-transparent text-zinc-400 hover:text-zinc-200'
            }`}
          >
            LinkedIn & WhatsApp Hub
          </button>
        </div>

        {/* Tab Content 1: Social Video Scheduler */}
        {activeTab === 'social' && (
          <div className="space-y-4 p-4 sm:p-5">
            {/* Scheduled post preview item */}
            <div className="rounded-xl border border-surface-700 bg-surface-850 p-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <div className="relative grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-lg bg-surface-950 border border-surface-700 text-accent-400">
                    <FeatureIcon name="video" />
                    <span className="absolute bottom-0.5 right-0.5 rounded bg-surface-900/90 px-1 text-[9px] font-mono text-zinc-300">
                      00:48
                    </span>
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-accent-500/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-300">
                        Scheduled
                      </span>
                      <span className="text-[11px] text-zinc-400">Today at 5:00 PM</span>
                    </div>
                    <h4 className="mt-0.5 text-xs font-semibold text-zinc-100 sm:text-sm">
                      10x Growth Strategies with AI Automation
                    </h4>
                  </div>
                </div>
                <span className="shrink-0 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/20">
                  Ready
                </span>
              </div>

              {/* Target platforms chip row */}
              <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-surface-750/70 pt-2.5">
                <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-500 mr-1">Target channels:</span>
                <span className="inline-flex items-center gap-1 rounded-md bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-300 ring-1 ring-inset ring-red-500/20">
                  <YouTubeMark className="h-3 w-3" /> YouTube Shorts
                </span>
                <span className="inline-flex items-center gap-1 rounded-md bg-pink-500/10 px-2 py-0.5 text-[10px] font-medium text-pink-300 ring-1 ring-inset ring-pink-500/20">
                  <InstagramMark className="h-3 w-3" /> Instagram Reels
                </span>
                <span className="inline-flex items-center gap-1 rounded-md bg-cyan-500/10 px-2 py-0.5 text-[10px] font-medium text-cyan-300 ring-1 ring-inset ring-cyan-500/20">
                  <TikTokMark className="h-3 w-3" /> TikTok
                </span>
                <span className="inline-flex items-center gap-1 rounded-md bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium text-blue-300 ring-1 ring-inset ring-blue-500/20">
                  <FacebookMark className="h-3 w-3" /> FB Reels
                </span>
              </div>

              {/* Video tools metadata preview */}
              <div className="mt-2.5 grid grid-cols-2 gap-2 text-[10px]">
                <div className="flex items-center gap-1.5 rounded-lg bg-surface-900 px-2.5 py-1.5 text-zinc-300">
                  <span className="text-accent-400">✂️</span>
                  <span>Trimmed: 00:02 — 00:46 (FFmpeg)</span>
                </div>
                <div className="flex items-center gap-1.5 rounded-lg bg-surface-900 px-2.5 py-1.5 text-zinc-300">
                  <span className="text-accent-400">✨</span>
                  <span>AI Hook: Generated & Formatted</span>
                </div>
              </div>
            </div>

            {/* AI Copy extraction snippet */}
            <div className="rounded-xl border border-indigo-500/20 bg-indigo-500/[0.04] p-3">
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-semibold text-indigo-200 flex items-center gap-1.5">
                  <FeatureIcon name="sparkles" />
                  Groq AI Platform Optimization
                </span>
                <span className="text-[10px] text-zinc-400">Auto-split by platform</span>
              </div>
              <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-300 line-clamp-2">
                &ldquo;Stop spending 8 hours a week re-uploading short videos. Here is the automated multi-channel system that publishes everywhere in 1-click... #automation #growth&rdquo;
              </p>
            </div>
          </div>
        )}

        {/* Tab Content 2: Outreach & WhatsApp */}
        {activeTab === 'outreach' && (
          <div className="space-y-3.5 p-4 sm:p-5">
            <div className="rounded-xl border border-surface-700 bg-surface-850 p-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span className="grid h-7 w-7 place-items-center rounded-lg bg-[#0a66c2] text-white">
                    <LinkedInMark className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <h4 className="text-xs font-semibold text-zinc-100">B2B Founder Outreach Campaign</h4>
                    <p className="text-[10px] text-zinc-500">18 profile visits · 6 connection notes sent today</p>
                  </div>
                </div>
                <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                  Active
                </span>
              </div>
              <div className="mt-3 space-y-1.5">
                <div className="flex items-center justify-between text-[11px] text-zinc-300">
                  <span className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-accent-400" />
                    Visit profile & scan recent posts
                  </span>
                  <span className="text-emerald-400 text-[10px]">Done (18)</span>
                </div>
                <div className="flex items-center justify-between text-[11px] text-zinc-300">
                  <span className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-indigo-400" />
                    Personalized connection note
                  </span>
                  <span className="text-indigo-300 text-[10px]">Paced (3m delay)</span>
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-emerald-500/15 bg-emerald-500/[0.05] p-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="grid h-7 w-7 place-items-center rounded-lg bg-[#25d366] text-white">
                    <WhatsAppMark className="h-3.5 w-3.5" />
                  </span>
                  <div>
                    <p className="text-xs font-medium text-zinc-200">WhatsApp Scanner: 4 Groups</p>
                    <p className="text-[10px] text-zinc-400">Scored 3 buyer-intent messages today (Score &gt; 85/100)</p>
                  </div>
                </div>
                <span className="rounded-md bg-surface-900 px-2 py-1 text-[10px] font-medium text-emerald-300">
                  Live
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Footer status bar inside mockup */}
        <div className="flex items-center justify-between border-t border-surface-800 bg-surface-950 px-4 py-2.5 text-[11px] text-zinc-400 sm:px-5">
          <span className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            Background Celery & Redis Queues Active
          </span>
          <span className="text-zinc-500">Human delays enabled</span>
        </div>
      </div>

      {/* Floating badge */}
      <div className="absolute -bottom-5 -left-4 hidden items-center gap-2.5 rounded-xl border border-white/10 bg-surface-850 px-3.5 py-2.5 shadow-xl shadow-black/40 sm:flex">
        <span className="grid h-8 w-8 place-items-center rounded-full bg-accent-500/15 text-accent-300">
          <CheckIcon />
        </span>
        <div>
          <p className="text-xs font-semibold text-zinc-100">Zero duplicate work</p>
          <p className="text-[10px] text-zinc-400">One hub for video, outreach & messaging</p>
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const { isAuthenticated } = useAuth();
  const { canSeeAdmin } = useAdminAccess();
  const primaryPath = isAuthenticated ? '/app' : '/signup';
  const primaryLabel = isAuthenticated ? 'Open App' : 'Start automating free';

  return (
    <div className="min-h-screen overflow-x-hidden bg-surface-950 text-zinc-100">
      {/* Background ambient lighting */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[880px] bg-[radial-gradient(circle_at_15%_15%,rgba(20,184,166,0.14),transparent_38%),radial-gradient(circle_at_85%_12%,rgba(99,102,241,0.12),transparent_34%),radial-gradient(circle_at_50%_45%,rgba(244,63,94,0.06),transparent_30%)]" />

      {/* Navigation Header */}
      <header className="sticky top-0 z-40 border-b border-white/[0.06] bg-surface-950/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-6">
          <Link to="/" className="flex items-center gap-2.5" aria-label="LinkEasy home">
            <img src="/favicon.svg" alt="" className="h-7 w-7" />
            <span className="text-lg font-bold tracking-tight text-zinc-50">
              Link<span className="text-accent-400">Easy</span>
            </span>
            <span className="hidden rounded-full bg-accent-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent-300 ring-1 ring-inset ring-accent-500/20 sm:inline-block">
              v1.0
            </span>
          </Link>

          <nav className="hidden items-center gap-6 text-sm text-zinc-400 md:flex" aria-label="Main navigation">
            <a href="#social-automation" className="transition hover:text-zinc-100">Social Automation</a>
            <a href="#features" className="transition hover:text-zinc-100">Features</a>
            <a href="#how-it-works" className="transition hover:text-zinc-100">How it works</a>
            <a href="#integrations" className="transition hover:text-zinc-100">Integrations</a>
          </nav>

          <div className="flex items-center gap-2.5">
            <Link
              to="/app"
              className="rounded-lg border border-surface-700 bg-surface-900 px-3.5 py-2 text-sm font-semibold text-zinc-300 transition hover:border-surface-600 hover:bg-surface-800 hover:text-zinc-100"
            >
              App
            </Link>
            {canSeeAdmin && (
              <Link to="/admin" className="btn-primary" data-testid="header-admin-button">
                Admin <ArrowIcon />
              </Link>
            )}
            {!isAuthenticated && (
              <Link
                to="/signup"
                className="hidden rounded-lg bg-accent-500 px-3.5 py-2 text-sm font-semibold text-surface-950 shadow-sm transition hover:bg-accent-400 sm:inline-flex"
              >
                Get started
              </Link>
            )}
          </div>
        </div>
      </header>

      <main className="relative">
        {/* Hero Section */}
        <section className="mx-auto grid max-w-7xl items-center gap-12 px-5 pb-20 pt-14 sm:px-6 sm:pt-20 lg:grid-cols-[1fr_1.05fr] lg:gap-14 lg:pb-28 lg:pt-24">
          <div className="relative z-10">
            {/* Pill Badge */}
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-accent-500/25 bg-accent-500/[0.08] px-3.5 py-1.5 text-xs font-medium text-accent-200">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-400 opacity-50" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent-400" />
              </span>
              Multi-Channel Social & Outreach Automation
            </div>

            {/* Main Headline */}
            <h1 className="max-w-2xl text-4xl font-extrabold leading-[1.08] tracking-[-0.035em] text-zinc-50 sm:text-5xl lg:text-[60px]">
              Connect your tools.{' '}
              <span className="bg-gradient-to-r from-accent-300 via-teal-300 to-indigo-400 bg-clip-text text-transparent">
                Automate your day.
              </span>
            </h1>

            {/* Subtitle */}
            <p className="mt-6 max-w-xl text-base leading-7 text-zinc-400 sm:text-lg sm:leading-8">
              Schedule short-form videos across YouTube Shorts, Instagram Reels, TikTok, and Facebook, run smart LinkedIn outreach, and monitor WhatsApp groups for high-intent leads—all managed from one unified dashboard.
            </p>

            {/* CTA Buttons */}
            <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
              <Link
                to="/app"
                className="btn-primary px-6 py-3.5 text-base shadow-lg shadow-accent-500/15"
                data-testid="landing-app-button"
              >
                App Dashboard
                <ArrowIcon />
              </Link>
              {canSeeAdmin && (
                <Link
                  to="/admin"
                  className="btn-secondary px-6 py-3.5 text-base"
                  data-testid="landing-admin-button"
                >
                  Admin Dashboard
                  <ArrowIcon />
                </Link>
              )}
              <a
                href="#social-automation"
                className="inline-flex items-center justify-center gap-2 rounded-lg px-5 py-3.5 text-sm font-semibold text-zinc-300 transition hover:bg-white/5 hover:text-white"
              >
                Social Automation Features
              </a>
            </div>

            {/* Value Checkmarks */}
            <div className="mt-8 grid grid-cols-2 gap-x-4 gap-y-2 text-xs text-zinc-400 sm:flex sm:flex-wrap sm:gap-x-5">
              {[
                '4-Platform Video Scheduler',
                'Groq AI Hook Generator',
                'In-Browser Video Trimmer',
                'Safe Human Pacing',
              ].map((item) => (
                <span key={item} className="inline-flex items-center gap-1.5">
                  <span className="text-accent-400"><CheckIcon /></span>
                  {item}
                </span>
              ))}
            </div>
          </div>

          <AutomationPreview />
        </section>

        {/* Supported Platforms / Integrations Section */}
        <section id="integrations" className="border-y border-white/[0.06] bg-white/[0.015]">
          <div className="mx-auto max-w-7xl px-5 py-12 sm:px-6">
            <div className="text-center">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-400">Universal Ecosystem</p>
              <h2 className="mt-1 text-xl font-bold text-zinc-100 sm:text-2xl">
                Every Growth Channel In One Single Workspace
              </h2>
              <p className="mt-2 text-xs text-zinc-400 sm:text-sm">
                Publish content, run campaigns, and engage audiences across today&apos;s leading networks.
              </p>
            </div>

            <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {platforms.map((platform) => (
                <div
                  key={platform.id}
                  className={`flex flex-col justify-between rounded-xl border p-4 transition hover:-translate-y-0.5 hover:shadow-lg ${platform.color}`}
                >
                  <div>
                    <div className="flex items-center justify-between">
                      <PlatformIcon id={platform.id} className={`h-6 w-6 ${platform.iconColor}`} />
                      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-emerald-300">
                        {platform.badge}
                      </span>
                    </div>
                    <h3 className="mt-3 text-sm font-semibold text-zinc-100">{platform.name}</h3>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-400">{platform.description}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Dedicated Social Automation Feature Deep Dive */}
        <section id="social-automation" className="relative mx-auto max-w-7xl px-5 py-24 sm:px-6 lg:py-28">
          <div className="max-w-3xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-red-500/20 bg-red-500/10 px-3 py-1 text-xs font-semibold text-red-300">
              <FeatureIcon name="video" />
              Social Video Automation Engine
            </div>
            <h2 className="mt-4 text-3xl font-extrabold tracking-tight text-zinc-50 sm:text-4xl lg:text-5xl">
              Publish short-form videos to 4 networks in 1 click.
            </h2>
            <p className="mt-4 text-base leading-7 text-zinc-400 sm:text-lg">
              Stop switching between YouTube Studio, Meta Creator Studio, TikTok, and video editing apps. LinkEasy gives you a centralized publishing suite equipped with in-browser FFmpeg trimming, Groq AI copy extraction, and automated queue execution.
            </p>
          </div>

          {/* Feature Cards Grid */}
          <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-2">
            {socialAutomationFeatures.map((feat) => (
              <article
                key={feat.title}
                className="group relative overflow-hidden rounded-2xl border border-surface-700 bg-surface-900/80 p-6 transition hover:border-accent-500/30 hover:bg-surface-850 sm:p-8"
              >
                <div className="flex items-start justify-between gap-4">
                  <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-accent-500/10 text-accent-300 ring-1 ring-accent-500/20 transition group-hover:bg-accent-500/20">
                    <FeatureIcon name={feat.icon} />
                  </span>
                  <span className="rounded-full bg-surface-800 px-2.5 py-1 text-[11px] font-medium text-zinc-300 ring-1 ring-surface-700">
                    {feat.badge}
                  </span>
                </div>

                <h3 className="mt-5 text-xl font-bold text-zinc-100">{feat.title}</h3>
                <p className="mt-2 text-sm leading-6 text-zinc-400">{feat.description}</p>

                <ul className="mt-5 space-y-2.5 border-t border-surface-800 pt-4">
                  {feat.bullets.map((bullet) => (
                    <li key={bullet} className="flex items-start gap-2 text-xs text-zinc-300">
                      <span className="mt-0.5 text-accent-400 shrink-0">
                        <CheckIcon />
                      </span>
                      <span>{bullet}</span>
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>

          {/* Interactive Flow Visualizer */}
          <div className="mt-14 overflow-hidden rounded-2xl border border-surface-700 bg-surface-900/60 p-6 sm:p-8">
            <div className="flex flex-col justify-between gap-4 border-b border-surface-800 pb-6 sm:flex-row sm:items-center">
              <div>
                <span className="text-[11px] font-semibold uppercase tracking-wider text-accent-400">End-to-End Pipeline</span>
                <h3 className="mt-1 text-lg font-bold text-zinc-100">How Short-Form Automation Works in LinkEasy</h3>
              </div>
              <Link
                to="/app/social-scheduler/schedule"
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-accent-300 hover:text-accent-200"
              >
                Open Social Scheduler <ArrowIcon />
              </Link>
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border border-surface-800 bg-surface-950/60 p-4">
                <span className="text-xs font-bold text-accent-400">01. Upload & Trim</span>
                <p className="mt-2 text-xs font-medium text-zinc-200">Upload video & trim in-place</p>
                <p className="mt-1 text-[11px] text-zinc-500">Fast server-side FFmpeg precision cut with live scrubber.</p>
              </div>
              <div className="rounded-xl border border-surface-800 bg-surface-950/60 p-4">
                <span className="text-xs font-bold text-indigo-400">02. AI Copy Extract</span>
                <p className="mt-2 text-xs font-medium text-zinc-200">Groq generates hooks & tags</p>
                <p className="mt-1 text-[11px] text-zinc-500">Auto-tailored titles for YouTube, IG, TikTok, and Facebook.</p>
              </div>
              <div className="rounded-xl border border-surface-800 bg-surface-950/60 p-4">
                <span className="text-xs font-bold text-pink-400">03. Pick Destinations</span>
                <p className="mt-2 text-xs font-medium text-zinc-200">Set playlists & privacy</p>
                <p className="mt-1 text-[11px] text-zinc-500">Select target YouTube playlists and cover thumbnails.</p>
              </div>
              <div className="rounded-xl border border-surface-800 bg-surface-950/60 p-4">
                <span className="text-xs font-bold text-emerald-400">04. Queue & Publish</span>
                <p className="mt-2 text-xs font-medium text-zinc-200">Autonomous execution</p>
                <p className="mt-1 text-[11px] text-zinc-500">Celery workers publish at scheduled times with auto-retry.</p>
              </div>
            </div>
          </div>
        </section>

        {/* Multi-Channel Outreach Section (LinkedIn & WhatsApp) */}
        <section className="border-t border-white/[0.06] bg-surface-900/30 py-24 sm:py-28">
          <div className="mx-auto max-w-7xl px-5 sm:px-6">
            <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
              <div>
                <span className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-400">
                  LinkedIn & WhatsApp Intelligence
                </span>
                <h2 className="mt-3 text-3xl font-bold tracking-tight text-zinc-50 sm:text-4xl">
                  Turn social attention into verified leads & conversations.
                </h2>
                <p className="mt-4 text-base leading-7 text-zinc-400">
                  While your video content grows brand awareness on YouTube and TikTok, LinkEasy powers your direct lead generation with intelligent LinkedIn campaigns and real-time WhatsApp keyword monitoring.
                </p>

                <div className="mt-8 space-y-4">
                  <div className="rounded-xl border border-surface-700 bg-surface-900 p-4">
                    <div className="flex items-center gap-3">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-[#0a66c2]/15 text-[#53a7f5]">
                        <LinkedInMark className="h-5 w-5" />
                      </span>
                      <div>
                        <h3 className="text-sm font-semibold text-zinc-100">Automated LinkedIn Multi-Touch Sequences</h3>
                        <p className="mt-0.5 text-xs text-zinc-400">
                          Automate profile visits, personalized connection invites, and follow-ups with natural human delay pacing to protect your account.
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-xl border border-surface-700 bg-surface-900 p-4">
                    <div className="flex items-center gap-3">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-[#25d366]/15 text-[#25d366]">
                        <WhatsAppMark className="h-5 w-5" />
                      </span>
                      <div>
                        <h3 className="text-sm font-semibold text-zinc-100">WhatsApp Group Scanner & Buyer Intent Alerting</h3>
                        <p className="mt-0.5 text-xs text-zinc-400">
                          Scan multiple WhatsApp groups for keyword queries, score intent from 0-100 with AI, and step in with live chat whenever a lead is ready.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Visual Card comparison */}
              <div className="space-y-4 rounded-2xl border border-surface-700 bg-surface-950/80 p-6 shadow-2xl">
                <div className="flex items-center justify-between border-b border-surface-800 pb-3">
                  <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400">Lead Pipeline Status</span>
                  <span className="text-xs text-accent-400 font-mono">Live Sync</span>
                </div>

                <div className="space-y-3">
                  <div className="flex items-center justify-between rounded-lg border border-surface-800 bg-surface-900 px-3.5 py-3">
                    <div className="flex items-center gap-3">
                      <span className="grid h-8 w-8 place-items-center rounded-lg bg-sky-500/10 text-sky-300">
                        <LinkedInMark className="h-4 w-4" />
                      </span>
                      <div>
                        <p className="text-xs font-semibold text-zinc-200">Tech Founders Campaign</p>
                        <p className="text-[11px] text-zinc-500">24 visited · 12 invited · 5 connected</p>
                      </div>
                    </div>
                    <span className="rounded-md bg-sky-500/10 px-2 py-0.5 text-[10px] font-medium text-sky-300">
                      Running
                    </span>
                  </div>

                  <div className="flex items-center justify-between rounded-lg border border-surface-800 bg-surface-900 px-3.5 py-3">
                    <div className="flex items-center gap-3">
                      <span className="grid h-8 w-8 place-items-center rounded-lg bg-emerald-500/10 text-emerald-300">
                        <WhatsAppMark className="h-4 w-4" />
                      </span>
                      <div>
                        <p className="text-xs font-semibold text-zinc-200">Startup Leads Filter</p>
                        <p className="text-[11px] text-zinc-500">3 high-intent messages matched</p>
                      </div>
                    </div>
                    <span className="rounded-md bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                      3 Matches
                    </span>
                  </div>

                  <div className="flex items-center justify-between rounded-lg border border-surface-800 bg-surface-900 px-3.5 py-3">
                    <div className="flex items-center gap-3">
                      <span className="grid h-8 w-8 place-items-center rounded-lg bg-red-500/10 text-red-300">
                        <YouTubeMark className="h-4 w-4" />
                      </span>
                      <div>
                        <p className="text-xs font-semibold text-zinc-200">Weekly Short-Form Schedule</p>
                        <p className="text-[11px] text-zinc-500">4 videos queued for this week</p>
                      </div>
                    </div>
                    <span className="rounded-md bg-accent-500/10 px-2 py-0.5 text-[10px] font-medium text-accent-300">
                      On Schedule
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* How It Works Section */}
        <section id="how-it-works" className="mx-auto max-w-7xl px-5 py-24 sm:px-6 lg:py-28">
          <div className="max-w-2xl">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-400">How it works</p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight text-zinc-50 sm:text-4xl">
              From connected to automated in three steps
            </h2>
            <p className="mt-4 text-base leading-7 text-zinc-400">
              No complicated automation map. Start with your accounts, define the outcome, and keep control from one dashboard.
            </p>
          </div>

          <div className="relative mt-14 grid gap-5 md:grid-cols-3">
            <div className="absolute left-[16.66%] right-[16.66%] top-8 hidden h-px bg-gradient-to-r from-accent-500/30 via-accent-400/50 to-indigo-500/30 md:block" />
            {steps.map((step) => (
              <article
                key={step.number}
                className="relative rounded-2xl border border-surface-700 bg-surface-900/60 p-6 transition hover:-translate-y-1 hover:border-accent-500/25 hover:bg-surface-900"
              >
                <span className="relative z-10 grid h-16 w-16 place-items-center rounded-2xl border border-accent-500/20 bg-surface-950 text-sm font-bold text-accent-300 shadow-lg shadow-black/20">
                  {step.number}
                </span>
                <span className="mt-4 block text-[11px] font-semibold uppercase tracking-wider text-accent-400">
                  {step.tagline}
                </span>
                <h3 className="mt-1 text-xl font-semibold text-zinc-100">{step.title}</h3>
                <p className="mt-2 text-sm leading-6 text-zinc-400">{step.body}</p>
              </article>
            ))}
          </div>
        </section>

        {/* Features Grid Section */}
        <section id="features" className="border-y border-white/[0.06] bg-surface-900/45">
          <div className="mx-auto max-w-7xl px-5 py-24 sm:px-6 lg:py-28">
            <div className="flex flex-col justify-between gap-5 md:flex-row md:items-end">
              <div className="max-w-2xl">
                <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-400">Built for the daily work</p>
                <h2 className="mt-3 text-3xl font-bold tracking-tight text-zinc-50 sm:text-4xl">
                  Less tab switching. More momentum.
                </h2>
              </div>
              <p className="max-w-md text-sm leading-6 text-zinc-400">
                Automate the repeatable parts of outreach, content distribution, and lead capture while keeping every important reply close at hand.
              </p>
            </div>

            <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {coreFeatures.map((feature, index) => (
                <article
                  key={feature.title}
                  className="group rounded-2xl border border-surface-700 bg-surface-900 p-6 transition hover:border-accent-500/25 hover:bg-surface-850 sm:p-7"
                >
                  <div className="flex items-start justify-between">
                    <span
                      className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl ${
                        index === 1
                          ? 'bg-indigo-500/10 text-indigo-300'
                          : index === 3
                            ? 'bg-emerald-500/10 text-emerald-300'
                            : index === 4
                              ? 'bg-pink-500/10 text-pink-300'
                              : 'bg-accent-500/10 text-accent-300'
                      }`}
                    >
                      <FeatureIcon name={feature.icon} />
                    </span>
                    <span className="rounded-md bg-surface-800 px-2 py-0.5 text-[10px] font-medium text-zinc-400">
                      {feature.tag}
                    </span>
                  </div>
                  <h3 className="mt-5 text-lg font-semibold text-zinc-100">{feature.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-zinc-400">{feature.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        {/* Call To Action Banner */}
        <section className="mx-auto max-w-7xl px-5 py-20 sm:px-6 lg:py-24">
          <div className="relative overflow-hidden rounded-3xl border border-accent-500/20 bg-[linear-gradient(125deg,rgba(20,184,166,0.14),rgba(22,22,26,0.92)_48%,rgba(99,102,241,0.12))] px-6 py-12 text-center sm:px-12 sm:py-16">
            <div className="pointer-events-none absolute left-1/2 top-0 h-56 w-96 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent-400/10 blur-3xl" />
            <div className="relative mx-auto max-w-2xl">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent-300">Put your tools to work</p>
              <h2 className="mt-4 text-3xl font-bold tracking-tight text-white sm:text-4xl">
                Your next productive day can run itself.
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-zinc-300 sm:text-base">
                Connect your social video accounts, start your first LinkedIn campaign, or automate WhatsApp scanning in minutes.
              </p>
              <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
                <Link to={primaryPath} className="btn-primary px-7 py-3.5 text-base shadow-lg shadow-accent-500/20">
                  {primaryLabel}
                  <ArrowIcon />
                </Link>
                <Link
                  to="/app/social-scheduler"
                  className="rounded-lg border border-surface-600 bg-surface-800/80 px-6 py-3.5 text-sm font-semibold text-zinc-200 transition hover:bg-surface-700 hover:text-white"
                >
                  Explore Social Scheduler
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-white/[0.06] bg-surface-950">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-6 px-5 py-10 text-sm text-zinc-500 sm:flex-row sm:px-6">
          <Link to="/" className="flex items-center gap-2 transition hover:text-zinc-300">
            <img src="/favicon.svg" alt="" className="h-5 w-5 opacity-70" />
            <span className="font-semibold text-zinc-300">LinkEasy</span>
          </Link>

          <div className="flex flex-col items-center gap-2 sm:items-end">
            <p className="text-center text-xs sm:text-right sm:text-sm">
              Social video automation, LinkedIn campaigns & WhatsApp intelligence in one workspace.
            </p>
            <nav className="flex gap-4 text-xs text-zinc-500" aria-label="Legal links">
              <Link to="/privacy" className="transition hover:text-zinc-300">Privacy Policy</Link>
              <Link to="/terms" className="transition hover:text-zinc-300">Terms of Service</Link>
              <Link to="/delete" className="transition hover:text-zinc-300">Data Deletion</Link>
            </nav>
          </div>
        </div>
      </footer>
    </div>
  );
}

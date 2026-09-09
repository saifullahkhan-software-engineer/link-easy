# LinkEasy app guide — pages

One `##` section per page (or tight group of pages). `Path:` lines are the
routes `navigate` is allowed to open; `Keywords:` feed the retrieval scorer.
Keep entries task-oriented: what the page does and when to send someone there.

## Accounts
- Path: /app/account
- Keywords: connect, account, accounts, settings, socials, integrations, link, unlink, disconnect

Central hub for everything the user has connected: LinkedIn, WhatsApp, Gmail
and social platforms (Instagram, Facebook, YouTube, TikTok). Go here to
connect a new channel, see which accounts are linked, or disconnect one.
Sub-pages: LinkedIn (/app/account/linkedin), WhatsApp (/app/account/whatsapp),
Gmail (/app/account/gmail) and each social platform (/app/account/social/instagram,
/app/account/social/facebook, /app/account/social/youtube, /app/account/social/tiktok).

## Dashboard (home)
- Path: /app/account
- Keywords: home, dashboard, overview, start

The app opens on Accounts. There is no separate customer dashboard page.

## Ultimate Inbox
- Path: /app/inbox
- Keywords: inbox, messages, chat, chats, dm, dms, reply, conversations, ultimate inbox

The unified inbox for every messaging channel. Channels: WhatsApp Chat
(/app/inbox/whatsapp), Instagram Chat (/app/inbox/instagram), Messenger Chat
(/app/inbox/messenger). WhatsApp Business Chat (/app/inbox/whatsapp-business)
is coming soon. Open a channel tab to read conversations and reply. WhatsApp
Chat needs its live browser running — open the WhatsApp tab to start it.

## WhatsApp Chat (live)
- Path: /app/inbox/whatsapp
- Keywords: whatsapp, whatsapp chat, live chat, whatsapp inbox, whatsapp messages

The user's personal WhatsApp, driven by a real browser session on the
backend. Opening the page starts the live browser (first load can take a
moment); after that the sidebar lists chats and the pane reads and sends
messages. Requires WhatsApp to be connected under Accounts first.

## Instagram Chat
- Path: /app/inbox/instagram
- Keywords: instagram, instagram chat, instagram dm, instagram inbox, instagram messages, ig

Direct messages for the connected Instagram business account(s), via the
official Meta API. Pick an account in the dropdown when several are
connected. Requires Instagram connected under Accounts → Socials.

## Messenger Chat
- Path: /app/inbox/messenger
- Keywords: messenger, facebook messages, facebook chat, fb messages, page messages

Facebook Page messages for the connected Page(s), via the official Meta API.
Requires Facebook connected under Accounts → Socials.

## Gmail Inbox
- Path: /app/gmail
- Keywords: gmail, email, mail, inbox unread, emails, google mail

Read the connected Gmail mailbox: unread counts, message list, reading pane,
labels and mark-as-read. Multiple mailboxes can be connected and picked in
the account dropdown. Compose a new mail at /app/gmail/compose.

## Gmail Compose
- Path: /app/gmail/compose
- Keywords: compose, write email, new email, send email, reply email

Write and send a new email from a connected Gmail mailbox.

## Social Scheduler
- Path: /app/social-scheduler
- Keywords: schedule, scheduler, post, posts, upload, shorts, reels, publishing, social media

Plan and publish content. Upload YouTube Shorts at
/app/social-scheduler/schedule, feed posts (Instagram, Facebook, TikTok,
YouTube) at /app/social-scheduler/posts, review what is queued at
/app/social-scheduler/queue, see everything on the calendar at
/app/social-scheduler/calendar, and check what already went out at
/app/social-scheduler/history. Facebook group sharing lives at
/app/social-scheduler/facebook-groups. The paste-and-split AI copy helper is
on the upload pages.

## LinkedIn Campaigns
- Path: /app/campaigns
- Keywords: linkedin campaign, campaigns, connection requests, outreach, create campaign

Automated LinkedIn outreach (connection requests + messages) to uploaded
leads. Create one at /app/campaigns/create, watch running jobs and results at
/app/campaigns. Requires a LinkedIn account connected under Accounts.

## Feed Scan
- Path: /app/feed-scroll
- Keywords: feed, feed scan, feed scroll, posts scan, scan feed, content scan, applied posts

Scans the LinkedIn feed automatically: reads posts, scores them and applies
saved actions. Manage jobs at /app/feed-scroll, create one at
/app/feed-scroll/create, and review results per job under
/app/feed-scroll/jobs/<jobId>.

## LinkedIn Live Chat
- Path: /app/linkedin-live
- Keywords: linkedin chat, linkedin messages, linkedin live, linkedin dm

Read and reply to LinkedIn messages through a live browser session, like
WhatsApp Chat but for LinkedIn. Requires LinkedIn connected.

## LinkedIn Profile Scan
- Path: /app/linkedin-profile
- Keywords: profile scan, pdf, profile, lead pdf, linkedin profile

Turns a LinkedIn profile into a downloadable PDF for lead records.

## WhatsApp Group Scan
- Path: /app/whatsapp-scanner
- Keywords: whatsapp group, group scan, whatsapp scanner, monitor groups, forward messages, filter jobs

Monitors chosen WhatsApp groups for messages matching filters and forwards
matches. Manage filters at /app/whatsapp-scanner, create one at
/app/whatsapp-scanner/create, results per filter under
/app/whatsapp-scanner/jobs/<filterId>.

## Operations Dashboard
- Path: /dashboard
- Keywords: operations, redis, queues, system queues, jobs dashboard

Staff/ops view with the Redis queue monitor at /dashboard/redis-queues. Admin
area lives under /admin. Customers normally never need these.

## Public pages
- Path: /
- Keywords: landing, login, signup, register, password, terms, privacy

Landing page, login (/login), signup (/signup), email verification
(/verify-email), password reset (/forgot-password, /reset-password), terms
(/terms), privacy (/privacy) and account deletion (/delete).

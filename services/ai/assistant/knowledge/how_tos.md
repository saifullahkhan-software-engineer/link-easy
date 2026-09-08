# LinkEasy app guide — how-tos

Task-oriented walkthroughs. Every path mentioned must exist in app_pages.md.

## Check new messages everywhere
- Keywords: new messages, unread, check messages, check inbox, notifications, who wrote, any messages, unread count

Ask the assistant "check my new messages" — it reads Instagram, Messenger,
WhatsApp and Gmail in one go and summarises what is waiting, with buttons to
jump to each channel. Instagram and Messenger need those platforms connected
under Accounts → Socials; Gmail needs a mailbox connected; WhatsApp Chat
needs its live browser running (opening /app/inbox/whatsapp starts it).

## Connect a channel
- Keywords: connect, add account, link account, connect instagram, connect facebook, connect whatsapp, connect gmail, connect linkedin, connect youtube, connect tiktok

Everything connects from Accounts (/app/account): LinkedIn, WhatsApp (QR
scan), Gmail (Google sign-in) and each social platform under the Socials
section. If a channel says "reconnect required", open its account page and
run the connect flow again.

## Send a message on Instagram or Messenger
- Keywords: reply instagram, reply messenger, send dm, answer message, reply message

Open Instagram Chat (/app/inbox/instagram) or Messenger Chat
(/app/inbox/messenger), pick the conversation, type and send. Replies go out
through the official Meta API from the connected account.

## Read or send Gmail
- Keywords: read email, check email, send email, gmail compose, unread mail

Gmail lives at /app/gmail — unread counts and the newest mail are right
there, and the page keeps checking while it is open. Compose at
/app/gmail/compose.

## Schedule a Shorts video or a post
- Keywords: schedule short, upload short, upload video, schedule post, upload post, reels, publish

Social Scheduler → Upload Shorts (/app/social-scheduler/schedule) or Upload
Posts (/app/social-scheduler/posts). Paste one multi-platform message and the
AI copy helper splits it into per-platform title/description/hashtags. Watch
the queue at /app/social-scheduler/queue and past results at
/app/social-scheduler/history.

## Start a LinkedIn outreach campaign
- Keywords: campaign, outreach, connection requests, leads campaign

Create one at /app/campaigns/create (pick the connected LinkedIn account and
lead list), then track it at /app/campaigns. Feed Scan jobs
(/app/feed-scroll) can feed the process by scanning the feed for leads.

## Monitor WhatsApp groups
- Keywords: monitor group, group filter, forward whatsapp, whatsapp automation

WhatsApp Group Scan: create a filter at /app/whatsapp-scanner/create, choose
the groups to watch and the match rules, then follow results at
/app/whatsapp-scanner. Matches can be forwarded automatically (paced to
avoid blocks).

## What the assistant can do
- Keywords: what can you do, help, capabilities, assistant, who are you

The assistant checks messages across channels, explains any page, walks
through how-tos, and takes the user straight to the right screen. Coming
later: analytics summaries and drafting replies.

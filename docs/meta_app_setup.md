# Meta app setup (Facebook Page + Instagram Reels)

Runbook for configuring the Meta (Facebook) app that powers the two social
cards in this app: **Facebook Page** and **Instagram Reels**. Both platforms
are the same Meta platform, so **one app serves both** — the app id/secret
you enter for Facebook and for Instagram can (and usually should) be
identical.

There is no "one app per Page/Instagram" restriction: a Page can be shared
with many apps, and any app with access to the linked Page can post to the
Instagram Business/Creator account behind it.

## 1. The app

Use one of these:

- **An existing app you can open** (app dashboard loads, Settings is
  editable). Keep it — it already has the product/redirects you configured.
  Nothing in the app needs to change when the code's Graph API version is
  bumped (the version travels in the request URL, not in the app).
- **A fresh personal app** (fastest if your current app is locked inside a
  Business you cannot access):
  1. developers.facebook.com → **My Apps → Create App** → type **"Other"**
     (Business & Marketing is optional; personal posting works without it).
  2. You are the app's **admin** automatically — that is all the "account
     registration" the app needs. There is no field where you register your
     personal Facebook account.
  3. The old, inaccessible app does not block anything — the Page is not
     "taken" by it. You may leave it (or archive it) untouched.

**App mode:** **Development mode is fine for personal use.** In
Development mode only the app's Admin/Developer/Tester accounts can
complete Facebook Login — your account qualifies as the app's admin. No App
Review, no Live mode, no business verification needed to post to your own
Page.

## 2. Products and permissions

- **Add product → Facebook Login** (for Web). This is the only product
  needed — Instagram permissions are granted by the same login.
- The app requests these scopes at sign-in (see `services/social/`):
  - Facebook Page: `pages_show_list, pages_read_engagement,
    pages_manage_posts, publish_video`
  - Instagram Reels: `instagram_basic, instagram_content_publish,
    pages_show_list, pages_read_engagement`
- In Development mode these work for the app's admin/developer/tester
  without review; on Facebook's consent screen, **approve every permission**
  (do not untick any).

## 3. Redirect URIs (must match character-for-character)

Add **both** to *Facebook Login → Settings → Valid OAuth Redirect URIs*
(scheme + host + path exactly as your deployment serves them):

```
https://<your-domain>/api/v1/social-scheduler/platforms/facebook/callback
https://<your-domain>/api/v1/social-scheduler/platforms/instagram/callback
```

(A trailing-slash or http/https mismatch makes the code exchange fail with
an "Invalid OAuth client"/redirect error.)

## 4. The Page

- *App settings → Business portfolio → Pages* → **add your Page** to the
  app.
- The account that **signs in** must administer the Page (it shows under
  Page → Settings → *Page access* / Business Suite → *Page roles*). A Page
  you only follow or like does not count.
- For Instagram: the Instagram account must be a **Business/Creator**
  account **linked to the Page** (Instagram → Menu → Settings → Accounts
  Center → Linked accounts → Instagram).

## 5. Environment (deployment)

| Variable | Value |
| --- | --- |
| `FACEBOOK_APP_ID` | the app id |
| `FACEBOOK_APP_SECRET` | the app secret |
| `FACEBOOK_REDIRECT_URI` | the facebook callback URL from step 3 |
| `INSTAGRAM_APP_ID` | same app id |
| `INSTAGRAM_APP_SECRET` | same app secret |
| `INSTAGRAM_REDIRECT_URI` | the instagram callback URL from step 3 |

## 6. Graph API version (code side)

All Meta calls derive from `GRAPH_API_VERSION` in
`services/social/meta_graph.py` (currently **v25.0**, supported through
July 2028). Meta sunsets versions on a ~2-year clock — v18.0 died
2026-01-26 (which silently broke the Instagram flow) and v20.0 dies
2026-09-24. When a version leaves the supported window, `GraphApiVersionTests`
fails the test suite; bump the constant (one line fixes both platforms) and
check the live window at developers.facebook.com/docs/graph-api/changelog.

## 7. Connecting (and reading failures)

1. In a browser **logged into the Page-admin Facebook account**, open the
   app's settings and click Connect on each card.
2. Approve **every** permission on Facebook's screen.
3. If a connect fails, the error shown is one of the app's own diagnoses
   (`services/social/facebook.py`, `services/social/instagram.py`):

| Error message (start) | Meaning / fix |
| --- | --- |
| `The signed-in Facebook account (Name) does not administer any Facebook Page…` | The account Facebook actually authenticated is not the Page-admin — the name is shown. Log into Facebook (top-right of facebook.com) with the account listed under Page access, then reconnect. |
| `…granted without the 'See a list of your Pages' permission…` | `pages_show_list` was unticked on the consent screen. Reconnect and allow all permissions. |
| `Facebook listed your Page(s) but issued no Page access token…` | The Page is listed but the role cannot create content. Check the role under Page access (needs content creation). |
| `…none of them has an Instagram Business/Creator account linked…` | Link the Instagram account to the Page via Accounts Center, then reconnect. |

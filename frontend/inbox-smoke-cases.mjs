import assert from 'node:assert/strict';

const waitFor = async (predicate, message = 'UI update') => {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    const value = predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`Timed out waiting for ${message}`);
};
const buttonNamed = (root, text) => [...root.querySelectorAll('button')].find((button) => button.textContent.trim() === text);
const setField = (window, input, value) => {
  const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(input, value);
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
};

/** Runs in the existing production-bundle/jsdom harness, with per-case API stubs. */
export function inboxSmokeCases({ AUTH_TOKENS, SOCIAL_API_STUBS, ACTIVE_ACCOUNT, json }) {
  const mainAccounts = {
    'GET /api/v1/linkedin/account': (res) => json(res, 200, ACTIVE_ACCOUNT),
    'GET /api/v1/whatsapp/status': (res) => json(res, 200, { status: 'connected', is_active: true }),
    'GET /api/v1/gmail/status': (res) => json(res, 200, { configured: true, connected: true, account_email: 'mail@test.dev' }),
  };
  const metaConnections = (res) => json(res, 200, [
    { platform: 'instagram', connected: true, configured: true, account_name: 'Test Instagram', account_id: 'ig-1' },
    { platform: 'facebook', connected: true, configured: true, account_name: 'Test Page', account_id: 'page-1' },
  ]);
  return [
    {
      name: 'workspace navigation — exact sidebar order, main/social sections and chat hierarchy',
      path: '/app/account', storage: AUTH_TOKENS,
      api: { ...SOCIAL_API_STUBS, ...mainAccounts },
      interact: async (window) => {
        const nav = window.document.querySelector('nav[aria-label="Workspace navigation"]');
        const order = [...nav.children].map((group) => group.querySelector(':scope > a, :scope > button').textContent.trim());
        assert.deepEqual(order, ['Accounts', 'Social Scheduler', 'Gmail', 'Ultimate Inbox', 'LinkedIn', 'WhatsApp Scan']);
        for (const label of order.slice(1)) buttonNamed(nav, label).click();
        const inbox = await waitFor(() => nav.querySelector('[aria-label="Ultimate Inbox pages"]'));
        assert.deepEqual([...inbox.querySelectorAll('a')].map((link) => link.getAttribute('href')), [
          '/app/inbox/whatsapp', '/app/inbox/instagram', '/app/inbox/messenger', '/app/inbox/whatsapp-business',
        ]);
        const scan = nav.querySelector('[aria-label="WhatsApp Scan pages"]');
        assert.equal(scan.querySelectorAll('a').length, 1);
        assert.equal(scan.querySelector('a').getAttribute('href'), '/app/whatsapp-scanner');
        assert.equal(nav.querySelector('a[href="/app/social-scheduler/settings"]'), null);
        assert.equal(nav.querySelectorAll('a[href="/app/inbox/whatsapp"]').length, 1);
        assert.ok(nav.querySelector('a[href="/app/account"][aria-current="page"]'));
        const main = window.document.querySelector('#main-accounts');
        assert.deepEqual([...main.querySelectorAll('h3')].map((node) => node.textContent), ['WhatsApp', 'LinkedIn', 'Gmail']);
        assert.ok(main.querySelector('a[href="/app/account/gmail"]'));
        const future = await waitFor(() => window.document.querySelector('[data-testid="platform-card-whatsapp-business"]'));
        assert.equal(future.querySelector('button').disabled, true);
      },
      mustContain: ['Main accounts', 'Socials', 'YouTube', 'Facebook', 'Instagram', 'TikTok', 'WhatsApp Business', 'Coming soon'],
      mustNotContain: ['Set up app credentials', 'Social scheduler sections'],
    },
    {
      name: 'workspace navigation — mobile drawer closes after selecting an inbox channel',
      path: '/app/account', storage: AUTH_TOKENS,
      api: { ...SOCIAL_API_STUBS, ...mainAccounts },
      interact: async (window) => {
        const doc = window.document;
        doc.querySelector('button[aria-label="Open menu"]').click();
        await waitFor(() => doc.body.style.overflow === 'hidden');
        buttonNamed(doc.querySelector('nav[aria-label="Workspace navigation"]'), 'Ultimate Inbox').click();
        const link = await waitFor(() => doc.querySelector('aside a[href="/app/inbox/instagram"]'));
        link.click();
        await waitFor(() => window.location.pathname === '/app/inbox/instagram' && doc.querySelector('button[aria-label="Open menu"]'));
        assert.notEqual(doc.body.style.overflow, 'hidden');
        assert.ok(doc.querySelector('aside a[href="/app/inbox/instagram"][aria-current="page"]'));
        doc.querySelector('button[aria-label="Open menu"]').click();
        await waitFor(() => doc.body.style.overflow === 'hidden');
        window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
        await waitFor(() => doc.body.style.overflow !== 'hidden');
      },
      mustContain: ['Instagram Chat', 'Go to social accounts'],
    },
    {
      name: 'accounts socials — OAuth success hands over to that platform’s manage page',
      path: '/app/social-scheduler/settings?platform=tiktok&connected=1&keep=yes', storage: AUTH_TOKENS,
      api: { ...SOCIAL_API_STUBS, ...mainAccounts },
      interact: async (window) => {
        await waitFor(() => window.location.pathname === '/app/account/social/tiktok');
        assert.equal(window.location.search, '?keep=yes');
      },
      mustContain: ['TikTok connected', 'TikTok accounts', 'Socials', '← Accounts'],
      mustNotContain: ['Social scheduler sections'],
    },
    {
      name: 'accounts socials — OAuth error is preserved and shown',
      path: '/app/social-scheduler/settings?platform=instagram&error=Permission%20not%20granted', storage: AUTH_TOKENS,
      api: { ...SOCIAL_API_STUBS, ...mainAccounts },
      mustContain: ['Instagram: Permission not granted', 'Instagram accounts'],
    },
    (() => {
      let connected = true;
      return {
        name: 'social manage page — connect errors recover and disconnect requires confirmation',
        path: '/app/account/social/youtube', storage: AUTH_TOKENS,
        api: {
          ...SOCIAL_API_STUBS, ...mainAccounts,
          'GET /api/v1/social-scheduler/platforms': (res) => json(res, 200, [{ platform: 'youtube', connected, configured: true, account_name: 'My Channel', accounts: connected ? [{ id: 'yt-1', platform: 'youtube', account_id: 'UC1', account_name: 'My Channel' }] : [] }]),
          'GET /api/v1/social-scheduler/platforms/youtube/auth-url': (res) => json(res, 502, { detail: 'Provider sign-in unavailable' }),
          'DELETE /api/v1/social-scheduler/platforms/youtube': (res) => { connected = false; json(res, 200, { message: 'YouTube disconnected' }); },
        },
        interact: async (window) => {
          const doc = window.document;
          const card = await waitFor(() => doc.querySelector('[data-testid="social-account-yt-1"]'));
          assert.ok(card.textContent.includes('My Channel'));
          const connectAnother = await waitFor(() => buttonNamed(doc, 'Connect another account'));
          connectAnother.click();
          await waitFor(() => doc.body.textContent.includes('Provider sign-in unavailable'));
          buttonNamed(card, 'Disconnect').click();
          await waitFor(() => doc.body.textContent.includes('Disconnect My Channel?'));
          assert.equal(connected, true);
          buttonNamed(doc, 'Keep connected').click();
          await waitFor(() => !doc.body.textContent.includes('Disconnect My Channel?'));
          assert.equal(connected, true);
          buttonNamed(card, 'Disconnect').click();
          await waitFor(() => doc.body.textContent.includes('Disconnect My Channel?'));
          [...doc.querySelectorAll('button')].filter((button) => button.textContent.trim() === 'Disconnect').at(-1).click();
          await waitFor(() => !connected && doc.body.textContent.includes('Connect your first YouTube account'));
        },
        mustContain: ['YouTube accounts', 'Connect YouTube', 'YouTube disconnected', '← Accounts'],
      };
    })(),
    (() => {
      let attempts = 0;
      return {
        name: 'accounts socials — failed connections load is retryable without hiding main accounts or future Business',
        path: '/app/account', storage: AUTH_TOKENS,
        api: {
          ...SOCIAL_API_STUBS, ...mainAccounts,
          'GET /api/v1/social-scheduler/platforms': (res) => ++attempts === 1 ? json(res, 503, { detail: 'Connections temporarily unavailable' }) : metaConnections(res),
        },
        interact: async (window) => {
          const doc = window.document;
          await waitFor(() => buttonNamed(doc, 'Retry connections'));
          assert.ok(doc.querySelector('#main-accounts').textContent.includes('Gmail'));
          assert.ok(doc.querySelector('[data-testid="platform-card-whatsapp-business"]'));
          buttonNamed(doc, 'Retry connections').click();
          const card = await waitFor(() => {
            const node = doc.querySelector('[data-testid="platform-card-instagram"]');
            return node && node.textContent.includes('1 account connected') ? node : null;
          });
          assert.ok(card.querySelector('a[href="/app/account/social/instagram"]'));
        },
        mustContain: ['1 account connected', 'Manage accounts', 'Coming soon'],
        // The hub summarises: account names live on the manage pages only.
        mustNotContain: ['Test Instagram', 'Test Page', 'Could not load your social connections.'],
      };
    })(),
    {
      name: 'social scheduler — connections link to Accounts and Settings is absent from scheduler navigation',
      path: '/app/social-scheduler', storage: AUTH_TOKENS, api: SOCIAL_API_STUBS,
      interact: async (window) => {
        const doc = window.document;
        await waitFor(() => doc.querySelector('main a[href="/app/account#socials"]'));
        assert.equal(doc.querySelector('a[href="/app/social-scheduler/settings"]'), null);
        assert.equal(doc.querySelector('nav[aria-label="Social scheduler sections"]').textContent.includes('Settings'), false);
      },
      mustContain: ['Connect platforms'],
    },
    {
      name: 'gmail accounts — legacy mailbox OAuth callback returns to account management',
      path: '/app/gmail?connected=1', storage: AUTH_TOKENS, api: mainAccounts,
      interact: async (window) => {
        await waitFor(() => window.location.pathname === '/app/account/gmail' && !window.location.search);
      },
      mustContain: ['Gmail connection', 'mail@test.dev', 'Connect another mailbox', 'Disconnect', 'Gmail connected'],
    },
    (() => {
      let connected = true;
      return {
        name: 'gmail accounts — disconnect stays under Accounts and returns the connect action',
        path: '/app/account/gmail', storage: AUTH_TOKENS,
        api: {
          'GET /api/v1/gmail/status': (res) => json(res, 200, { connected, configured: true, account_email: 'mail@test.dev' }),
          'DELETE /api/v1/gmail/connection': (res) => { connected = false; json(res, 200, { message: 'Disconnected' }); },
        },
        interact: async (window) => {
          const doc = window.document;
          const disconnect = await waitFor(() => buttonNamed(doc, 'Disconnect'));
          disconnect.click();
          await waitFor(() => doc.body.textContent.includes('Disconnect mail@test.dev?'));
          assert.equal(connected, true);
          [...doc.querySelectorAll('button')].filter((button) => button.textContent.trim() === 'Disconnect').at(-1).click();
          await waitFor(() => !connected && buttonNamed(doc, 'Connect Gmail'));
          assert.equal(window.location.pathname, '/app/account/gmail');
        },
        mustContain: ['Connect Gmail', 'Gmail disconnected'],
      };
    })(),
    {
      name: 'ultimate inbox — Instagram connection prompt belongs to Accounts',
      path: '/app/inbox/instagram', storage: AUTH_TOKENS, api: SOCIAL_API_STUBS,
      mustContain: ['Ultimate Inbox', 'Instagram Chat', 'Connect Instagram to get started', 'Go to social accounts'],
      mustNotContain: ['Write a reply…'],
    },
    {
      name: 'ultimate inbox — Messenger uses a Facebook Page, not personal chat',
      path: '/app/inbox/messenger', storage: AUTH_TOKENS, api: SOCIAL_API_STUBS,
      mustContain: ['Messenger Chat', 'Connect Facebook Page to get started', 'not a personal Facebook inbox', 'Go to social accounts'],
    },
    ...['instagram', 'messenger'].map((channel) => {
      const replies = [];
      return {
        name: `ultimate inbox — ${channel} reads and sends acknowledged text replies`,
        path: `/app/inbox/${channel}`, storage: AUTH_TOKENS,
        api: {
          'GET /api/v1/social-scheduler/platforms': metaConnections,
          [`GET /api/v1/inbox/${channel}/conversations`]: (res) => json(res, 200, { conversations: [{ id: 'c1', name: 'Alice', preview: 'Hello there' }] }),
          [`GET /api/v1/inbox/${channel}/messages`]: (res) => json(res, 200, { messages: [{ id: 'm1', text: 'Hello there', sender: 'Alice', outgoing: false }] }),
          [`POST /api/v1/inbox/${channel}/messages`]: (res, req) => {
            let raw = '';
            req.on('data', (chunk) => { raw += chunk; });
            req.on('end', () => { replies.push(JSON.parse(raw)); json(res, 200, { message_id: 'sent-1' }); });
          },
        },
        interact: async (window) => {
          const doc = window.document;
          const row = await waitFor(() => [...doc.querySelectorAll('aside button')].find((button) => button.textContent.includes('Alice')));
          row.click();
          const input = await waitFor(() => { const input = doc.querySelector('textarea'); return input && !input.disabled && input; });
          // Clicking the active row must not blank the conversation indefinitely.
          row.click();
          await new Promise((resolve) => setTimeout(resolve, 40));
          assert.equal(input.disabled, false);
          setField(window, input, 'Thanks for reaching out');
          const send = await waitFor(() => { const button = buttonNamed(doc, 'Send reply'); return button && !button.disabled && button; });
          send.click();
          await waitFor(() => replies.length === 1 && input.value === '');
          assert.deepEqual(replies, [{ conversation_id: 'c1', text: 'Thanks for reaching out' }]);
          assert.ok(doc.querySelector('section[aria-label="Chat messages"]').textContent.includes('Thanks for reaching out'));
          assert.ok(doc.querySelector(`aside a[href="/app/inbox/${channel}"][aria-current="page"]`));
        },
        mustContain: ['Thanks for reaching out', 'Latest 20 messages', 'Refresh inbox'],
      };
    }),
    (() => {
      let reads = 0;
      return {
        name: 'ultimate inbox — provider read and send errors keep a retry and the unsent draft',
        path: '/app/inbox/messenger', storage: AUTH_TOKENS,
        api: {
          'GET /api/v1/social-scheduler/platforms': metaConnections,
          'GET /api/v1/inbox/messenger/conversations': (res) => ++reads === 1 ? json(res, 403, { detail: 'Messaging permission required' }) : json(res, 200, { conversations: [{ id: 'c1', name: 'Alice' }] }),
          'GET /api/v1/inbox/messenger/messages': (res) => json(res, 200, { messages: [{ id: 'm1', text: 'Can you help?', sender: 'Alice' }] }),
          'POST /api/v1/inbox/messenger/messages': (res) => json(res, 403, { detail: 'Reply window has closed' }),
        },
        interact: async (window) => {
          const doc = window.document;
          await waitFor(() => buttonNamed(doc, 'Retry conversations'));
          assert.ok(doc.body.textContent.includes('Messaging permission required'));
          buttonNamed(doc, 'Retry conversations').click();
          const row = await waitFor(() => [...doc.querySelectorAll('aside button')].find((button) => button.textContent.includes('Alice')));
          row.click();
          const input = await waitFor(() => { const input = doc.querySelector('textarea'); return input && !input.disabled && input; });
          setField(window, input, 'Unsent reply');
          const send = await waitFor(() => { const button = buttonNamed(doc, 'Send reply'); return button && !button.disabled && button; });
          send.click();
          await waitFor(() => doc.body.textContent.includes('Reply window has closed'));
          assert.equal(input.value, 'Unsent reply');
          assert.equal(doc.querySelectorAll('section[aria-label="Chat messages"] .rounded-br-sm').length, 0);
        },
        mustContain: ['Reply window has closed', 'Unsent reply'],
      };
    })(),
    {
      name: 'ultimate inbox — WhatsApp Business is clearly future-only',
      path: '/app/inbox/whatsapp-business', storage: AUTH_TOKENS,
      mustContain: ['WhatsApp Business Chat', 'Coming soon', 'not available yet', 'Open WhatsApp Chat'],
      mustNotContain: ['Write a reply…', 'Send reply', 'Start live chat'],
    },
    ...['instagram', 'messenger', 'whatsapp-business'].map((channel) => ({
      name: `ultimate inbox — ${channel} requires sign-in`,
      path: `/app/inbox/${channel}`, mustContain: ['Log in', 'Forgot password?'],
    })),
  ];
}

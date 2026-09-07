// The sidebar and inbox tabs share one ordered list of channels.
export const INBOX_CHANNELS = [
  { id: 'whatsapp', to: '/app/inbox/whatsapp', label: 'WhatsApp Chat', accountPath: '/app/account/whatsapp' },
  { id: 'instagram', to: '/app/inbox/instagram', label: 'Instagram Chat', platform: 'instagram', accountPath: '/app/account/social/instagram' },
  { id: 'messenger', to: '/app/inbox/messenger', label: 'Messenger Chat', platform: 'facebook', accountPath: '/app/account/social/facebook' },
  { id: 'whatsapp-business', to: '/app/inbox/whatsapp-business', label: 'WhatsApp Business Chat', comingSoon: true, accountPath: '/app/account#socials' },
];

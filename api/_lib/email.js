import { Resend } from 'resend';

let cachedResend = null;

function getResend() {
  if (cachedResend) return cachedResend;
  const key = process.env.RESEND_API_KEY;
  if (!key) throw new Error('Missing RESEND_API_KEY environment variable.');
  cachedResend = new Resend(key);
  return cachedResend;
}

const FROM = process.env.NEWSLETTER_FROM || 'DPDPIndia.in <newsletter@dpdpindia.in>';
const SITE_URL = process.env.SITE_URL || 'https://www.dpdpindia.in';

const wrap = (bodyHtml) => `
<div style="font-family:Georgia,'Source Serif 4',serif;max-width:560px;margin:0 auto;padding:32px 24px;color:#2D3D4E;">
  <div style="font-family:Georgia,serif;font-weight:700;font-size:18px;color:#1B2B3A;margin-bottom:24px;">
    DPDP<span style="color:#D4760A;">India</span>.in
  </div>
  ${bodyHtml}
  <div style="margin-top:32px;padding-top:16px;border-top:1px solid #e5e0d5;font-size:12px;color:#8a8378;">
    DPDPIndia.in — India's independent research portal on the DPDP Act, IT Act, Aadhaar/eKYC and cyber crime.
  </div>
</div>`;

export async function sendConfirmationEmail({ to, name, confirmUrl }) {
  const resend = getResend();
  const greeting = name ? `Hi ${name},` : 'Hi,';
  const html = wrap(`
    <p style="font-size:15px;line-height:1.6;">${greeting}</p>
    <p style="font-size:15px;line-height:1.6;">Please confirm your email to start receiving DPDPIndia.in's Monthly Intelligence Briefing — one issue a month, free, no spam.</p>
    <p style="margin:28px 0;">
      <a href="${confirmUrl}" style="display:inline-block;background:#1B2B3A;color:#fff;text-decoration:none;padding:12px 22px;border-radius:6px;font-family:Arial,sans-serif;font-weight:600;font-size:14px;">Confirm my subscription →</a>
    </p>
    <p style="font-size:13px;line-height:1.6;color:#5A5449;">If you didn't request this, you can safely ignore this email — you won't be subscribed unless you click the button above.</p>
  `);
  return resend.emails.send({
    from: FROM,
    to,
    subject: 'Confirm your subscription to DPDPIndia.in',
    html,
  });
}

export async function sendWelcomeEmail({ to, name, unsubscribeUrl }) {
  const resend = getResend();
  const greeting = name ? `Hi ${name},` : 'Hi,';
  const html = wrap(`
    <p style="font-size:15px;line-height:1.6;">${greeting}</p>
    <p style="font-size:15px;line-height:1.6;">You're confirmed. You'll get the Monthly Intelligence Briefing in your inbox the day each new issue goes live — DPDP Act developments, cyber crime rulings, and regulatory moves, with primary sources.</p>
    <p style="font-size:15px;line-height:1.6;">In the meantime, read the latest issue: <a href="${SITE_URL}/monthly-briefing.html" style="color:#D4760A;">dpdpindia.in/monthly-briefing.html</a></p>
    <p style="font-size:12px;line-height:1.6;color:#8a8378;margin-top:24px;"><a href="${unsubscribeUrl}" style="color:#8a8378;">Unsubscribe</a> any time — no questions asked.</p>
  `);
  return resend.emails.send({
    from: FROM,
    to,
    subject: "You're subscribed — DPDPIndia.in Monthly Briefing",
    html,
  });
}

export async function sendIssueEmail({ to, name, issueTitle, issueUrl, issueDeck, unsubscribeUrl }) {
  const resend = getResend();
  const greeting = name ? `Hi ${name},` : 'Hi,';
  const html = wrap(`
    <p style="font-size:15px;line-height:1.6;">${greeting}</p>
    <p style="font-size:15px;line-height:1.6;">The latest DPDPIndia.in Monthly Briefing is live:</p>
    <p style="font-family:Georgia,serif;font-size:19px;font-weight:700;color:#1B2B3A;line-height:1.35;margin:16px 0;">${issueTitle}</p>
    ${issueDeck ? `<p style="font-size:14px;line-height:1.7;color:#5A5449;">${issueDeck}</p>` : ''}
    <p style="margin:28px 0;">
      <a href="${issueUrl}" style="display:inline-block;background:#1B2B3A;color:#fff;text-decoration:none;padding:12px 22px;border-radius:6px;font-family:Arial,sans-serif;font-weight:600;font-size:14px;">Read the full briefing →</a>
    </p>
    <p style="font-size:12px;line-height:1.6;color:#8a8378;margin-top:24px;"><a href="${unsubscribeUrl}" style="color:#8a8378;">Unsubscribe</a> any time — no questions asked.</p>
  `);
  return resend.emails.send({
    from: FROM,
    to,
    subject: `📰 ${issueTitle}`,
    html,
  });
}

import { getSupabaseAdmin } from './_lib/supabase.js';
import { sendIssueEmail } from './_lib/email.js';

const SITE_URL = process.env.SITE_URL || 'https://www.dpdpindia.in';
const BATCH_SIZE = 50; // stay comfortably under Resend's per-request/rate limits
const BATCH_DELAY_MS = 1200;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Sends a new-issue email to every confirmed subscriber.
 *
 * Protected by a shared secret (CAMPAIGN_SECRET env var) — trigger with:
 *
 *   curl -X POST https://www.dpdpindia.in/api/send-campaign \
 *     -H "x-admin-secret: <CAMPAIGN_SECRET>" \
 *     -H "content-type: application/json" \
 *     -d '{"issueTitle":"...", "issueUrl":"https://www.dpdpindia.in/monthly-briefing-2026-10.html", "issueDeck":"..."}'
 *
 * Note: Resend's free tier caps at 100 emails/day, 3,000/month. Once the
 * confirmed subscriber count regularly exceeds ~100, a single campaign send
 * may need to span more than one day, or the Resend plan needs upgrading.
 */
export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const providedSecret = req.headers['x-admin-secret'];
  const expectedSecret = process.env.CAMPAIGN_SECRET;
  if (!expectedSecret) {
    return res.status(500).json({ error: 'CAMPAIGN_SECRET is not configured on the server.' });
  }
  if (!providedSecret || providedSecret !== expectedSecret) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const { issueTitle, issueUrl, issueDeck, dryRun } = req.body || {};
  if (!issueTitle || !issueUrl) {
    return res.status(400).json({ error: 'issueTitle and issueUrl are required.' });
  }

  try {
    const supabase = getSupabaseAdmin();

    // Paginate through all confirmed subscribers (Supabase caps a single
    // query at 1000 rows by default).
    const subscribers = [];
    let from = 0;
    const PAGE = 1000;
    while (true) {
      const { data, error } = await supabase
        .from('newsletter_subscribers')
        .select('email, name, unsubscribe_token')
        .eq('status', 'confirmed')
        .range(from, from + PAGE - 1);
      if (error) throw error;
      subscribers.push(...data);
      if (data.length < PAGE) break;
      from += PAGE;
    }

    if (dryRun) {
      return res.status(200).json({ dryRun: true, wouldSendTo: subscribers.length });
    }

    let sent = 0;
    const failures = [];

    for (let i = 0; i < subscribers.length; i += BATCH_SIZE) {
      const batch = subscribers.slice(i, i + BATCH_SIZE);
      const results = await Promise.allSettled(
        batch.map((sub) =>
          sendIssueEmail({
            to: sub.email,
            name: sub.name,
            issueTitle,
            issueUrl,
            issueDeck,
            unsubscribeUrl: `${SITE_URL}/api/unsubscribe?token=${sub.unsubscribe_token}`,
          })
        )
      );
      results.forEach((r, idx) => {
        if (r.status === 'fulfilled') {
          sent += 1;
        } else {
          failures.push({ email: batch[idx].email, error: String(r.reason) });
        }
      });
      if (i + BATCH_SIZE < subscribers.length) await sleep(BATCH_DELAY_MS);
    }

    return res.status(200).json({
      totalConfirmedSubscribers: subscribers.length,
      sent,
      failed: failures.length,
      failures: failures.slice(0, 20), // cap payload size
    });
  } catch (err) {
    console.error('send-campaign: unexpected error', err);
    return res.status(500).json({ error: 'Server error', detail: String(err) });
  }
}

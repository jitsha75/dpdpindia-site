import { getSupabaseAdmin } from './_lib/supabase.js';
import { sendWelcomeEmail } from './_lib/email.js';

const SITE_URL = process.env.SITE_URL || 'https://www.dpdpindia.in';

function redirect(res, path) {
  res.writeHead(303, { Location: `${SITE_URL}${path}` });
  res.end();
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).send('Method not allowed');
  }

  const token = String(req.query.token || '').trim();
  if (!token) {
    return redirect(res, '/subscribe-status.html?state=error&reason=missing_token');
  }

  try {
    const supabase = getSupabaseAdmin();

    const { data: subscriber, error: lookupError } = await supabase
      .from('newsletter_subscribers')
      .select('id, email, name, status, unsubscribe_token')
      .eq('confirm_token', token)
      .maybeSingle();

    if (lookupError) throw lookupError;
    if (!subscriber) {
      return redirect(res, '/subscribe-status.html?state=error&reason=invalid_token');
    }

    if (subscriber.status === 'unsubscribed') {
      // They confirmed, then unsubscribed later, then clicked an old link.
      return redirect(res, '/subscribe-status.html?state=unsubscribed');
    }

    if (subscriber.status !== 'confirmed') {
      const { error: updateError } = await supabase
        .from('newsletter_subscribers')
        .update({ status: 'confirmed', confirmed_at: new Date().toISOString() })
        .eq('id', subscriber.id);
      if (updateError) throw updateError;

      const unsubscribeUrl = `${SITE_URL}/api/unsubscribe?token=${subscriber.unsubscribe_token}`;
      try {
        await sendWelcomeEmail({ to: subscriber.email, name: subscriber.name, unsubscribeUrl });
      } catch (emailErr) {
        // Don't block confirmation on the welcome email failing to send.
        console.error('confirm: failed to send welcome email', emailErr);
      }
    }

    return redirect(res, '/subscribe-status.html?state=confirmed');
  } catch (err) {
    console.error('confirm: unexpected error', err);
    return redirect(res, '/subscribe-status.html?state=error&reason=server');
  }
}

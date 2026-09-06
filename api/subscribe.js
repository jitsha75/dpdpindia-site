import { getSupabaseAdmin } from './_lib/supabase.js';
import { sendConfirmationEmail } from './_lib/email.js';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SITE_URL = process.env.SITE_URL || 'https://www.dpdpindia.in';

function redirect(res, path) {
  res.writeHead(303, { Location: `${SITE_URL}${path}` });
  res.end();
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).send('Method not allowed');
  }

  try {
    const body = req.body || {};
    const email = String(body.email || '').trim().toLowerCase();
    const name = body.name ? String(body.name).trim().slice(0, 200) : null;
    const role = body.role ? String(body.role).trim().slice(0, 100) : null;
    const source = body.source ? String(body.source).trim().slice(0, 60) : 'unknown';

    if (!email || !EMAIL_RE.test(email)) {
      return redirect(res, '/subscribe-status.html?state=error&reason=invalid_email');
    }

    const supabase = getSupabaseAdmin();

    const { data: existing, error: lookupError } = await supabase
      .from('newsletter_subscribers')
      .select('id, status, confirm_token, name')
      .eq('email', email)
      .maybeSingle();

    if (lookupError) throw lookupError;

    let confirmToken;
    let subscriberName = name;

    if (!existing) {
      const { data: inserted, error: insertError } = await supabase
        .from('newsletter_subscribers')
        .insert({ email, name, role, source, status: 'pending' })
        .select('confirm_token')
        .single();
      if (insertError) throw insertError;
      confirmToken = inserted.confirm_token;
    } else if (existing.status === 'confirmed') {
      // Already subscribed — no need to send anything, just tell them.
      return redirect(res, '/subscribe-status.html?state=already');
    } else {
      // pending or unsubscribed -> re-send a confirmation, reset to pending.
      // Existing confirm_token is left untouched (not included in the update).
      const { data: updated, error: updateError } = await supabase
        .from('newsletter_subscribers')
        .update({
          status: 'pending',
          name: name || existing.name,
          role,
          source,
        })
        .eq('id', existing.id)
        .select('confirm_token, name')
        .single();
      if (updateError) throw updateError;
      confirmToken = updated.confirm_token;
      subscriberName = updated.name;
    }

    const confirmUrl = `${SITE_URL}/api/confirm?token=${confirmToken}`;

    try {
      await sendConfirmationEmail({ to: email, name: subscriberName, confirmUrl });
    } catch (emailErr) {
      console.error('subscribe: failed to send confirmation email', emailErr);
      // The subscriber row exists either way — surface a distinct state so
      // this is visible in testing rather than silently pretending success.
      return redirect(res, '/subscribe-status.html?state=error&reason=email_failed');
    }

    return redirect(res, '/subscribe-status.html?state=pending');
  } catch (err) {
    console.error('subscribe: unexpected error', err);
    return redirect(res, '/subscribe-status.html?state=error&reason=server');
  }
}

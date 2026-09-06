import { getSupabaseAdmin } from './_lib/supabase.js';

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
      .select('id, status')
      .eq('unsubscribe_token', token)
      .maybeSingle();

    if (lookupError) throw lookupError;
    if (!subscriber) {
      return redirect(res, '/subscribe-status.html?state=error&reason=invalid_token');
    }

    if (subscriber.status !== 'unsubscribed') {
      const { error: updateError } = await supabase
        .from('newsletter_subscribers')
        .update({ status: 'unsubscribed', unsubscribed_at: new Date().toISOString() })
        .eq('id', subscriber.id);
      if (updateError) throw updateError;
    }

    return redirect(res, '/subscribe-status.html?state=unsubscribed');
  } catch (err) {
    console.error('unsubscribe: unexpected error', err);
    return redirect(res, '/subscribe-status.html?state=error&reason=server');
  }
}

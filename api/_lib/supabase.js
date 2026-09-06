import { createClient } from '@supabase/supabase-js';

let cachedClient = null;

/**
 * Server-side Supabase client using the service-role key.
 * NEVER import this file into anything that ships to the browser —
 * the service-role key bypasses Row Level Security entirely.
 */
export function getSupabaseAdmin() {
  if (cachedClient) return cachedClient;

  const url = process.env.SUPABASE_URL;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !serviceKey) {
    throw new Error(
      'Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variable.'
    );
  }

  cachedClient = createClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
  return cachedClient;
}

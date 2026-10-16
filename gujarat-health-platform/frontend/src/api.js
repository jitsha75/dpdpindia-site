// Central fetch helper for every backend call.
//
// IMPORTANT: every call is a RELATIVE path (`/api/<service-name>/<route>`).
// The browser must be loaded through the API gateway (http://localhost:8300)
// so these relative paths resolve to the gateway, which strips the
// `/api/<service-name>` prefix and forwards to the right container. Never
// hardcode a host:port here.

export const SERVICES = [
  "auth-service", "workflow-engine-service", "requisition-service",
  "tender-publishing-service", "vendor-master-service", "bid-evaluation-service",
  "contract-management-service", "dispute-management-service", "reference-doc-service",
  "dpdp-service", "audit-trail-service", "reporting-service", "notification-service",
  "ocr-icr-service", "ingestion-pipeline-service", "rag-orchestrator-service",
  "ai-usecase-service", "model-lifecycle-service", "project-intelligence-service",
  "iam-adapter-service", "portal-integration-service",
  "tender-doc-library-service", "data-governance-service",
];

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

/**
 * apiFetch("/api/<service>/<route>", { method, body, token })
 * - JSON in, JSON out.
 * - Never throws a raw TypeError for network failures — always throws an
 *   ApiError with a human-readable message so callers can render a friendly
 *   state instead of crashing.
 */
export async function apiFetch(path, { method = "GET", body, token, headers = {} } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    throw new ApiError(
      "Could not reach the service. It may still be booting, or the gateway is unreachable.",
      0,
      null
    );
  }

  const raw = await res.text();
  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw; // non-JSON body — keep as text so callers can still show something
    }
  }

  if (!res.ok) {
    const detail = data && typeof data === "object" ? data.detail ?? data.message : null;
    const message =
      (typeof detail === "string" && detail) ||
      (detail ? JSON.stringify(detail) : null) ||
      `Request failed (HTTP ${res.status})`;
    throw new ApiError(message, res.status, data);
  }

  return data;
}

/** Best-effort GET that never throws — resolves to {ok, data, error}. Handy for dashboards/boards. */
export async function safeGet(path, opts) {
  try {
    const data = await apiFetch(path, opts);
    return { ok: true, data, error: null };
  } catch (e) {
    return { ok: false, data: null, error: e.message || "Unknown error" };
  }
}

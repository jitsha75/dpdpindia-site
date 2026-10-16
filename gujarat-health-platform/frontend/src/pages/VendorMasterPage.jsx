import React, { useCallback, useEffect, useState } from "react";
import { apiFetch, safeGet } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Card, Loading, ErrorState, EmptyState, Badge } from "../components/Ui.jsx";

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.vendors)) return data.vendors;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

export default function VendorMasterPage() {
  const { token } = useAuth();
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [raw, setRaw] = useState(null);
  const [checkResults, setCheckResults] = useState({});
  const [checkingId, setCheckingId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const res = await safeGet("/api/vendor-master-service/vendors", { token });
    if (res.ok) {
      const normalized = normalizeList(res.data);
      setVendors(normalized);
      setRaw(normalized.length === 0 ? res.data : null);
    } else {
      setError(res.error);
      setVendors([]);
    }
    setLoading(false);
  }, [token]);

  useEffect(() => {
    load();
  }, [load]);

  async function runBlacklistCheck(id) {
    setCheckingId(id);
    try {
      const data = await apiFetch(`/api/vendor-master-service/vendors/${id}/blacklist-check`, { token });
      setCheckResults((prev) => ({ ...prev, [id]: data }));
    } catch (err) {
      setCheckResults((prev) => ({ ...prev, [id]: { error: err.message } }));
    } finally {
      setCheckingId(null);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Vendor Master</h1>
        <p>Seeded vendor register with MSE/Make-in-India classification and blacklist status.</p>
      </div>

      <Card title="Vendors">
        {loading && <Loading />}
        {error && <ErrorState message={error} onRetry={load} />}
        {!loading && !error && vendors.length === 0 && <EmptyState message="No vendors returned yet." />}
        {!loading && !error && vendors.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>MSE</th>
                <th>Class (local content)</th>
                <th>Blacklisted</th>
                <th>Blacklist check</th>
              </tr>
            </thead>
            <tbody>
              {vendors.map((v) => {
                const result = checkResults[v.id];
                return (
                  <tr key={v.id ?? v.name}>
                    <td>{v.name ?? "—"}</td>
                    <td>
                      {v.mse_flag ? (
                        <Badge tone="ok">MSE{v.mse_category ? ` · ${v.mse_category}` : ""}</Badge>
                      ) : (
                        <Badge tone="neutral">No</Badge>
                      )}
                    </td>
                    <td>{v.class_local ?? "—"}</td>
                    <td>
                      {v.blacklisted ? (
                        <Badge tone="error">Blacklisted{v.blacklist_reason ? ` · ${v.blacklist_reason}` : ""}</Badge>
                      ) : (
                        <Badge tone="ok">Clear</Badge>
                      )}
                    </td>
                    <td>
                      <button
                        className="btn btn-ghost btn-sm"
                        disabled={v.id === undefined || checkingId === v.id}
                        onClick={() => runBlacklistCheck(v.id)}
                      >
                        {checkingId === v.id ? "Checking…" : "Check"}
                      </button>
                      {result && (
                        result.error ? (
                          <div className="hint hint-error">{result.error}</div>
                        ) : (
                          <div className="hint">
                            {result.blacklisted ? `Blacklisted — ${result.reason ?? "no reason given"}` : "Clear"}
                          </div>
                        )
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {raw && <p className="hint">vendor-master-service returned a response without a recognisable vendor list yet (it may still be a stub).</p>}
      </Card>
    </div>
  );
}

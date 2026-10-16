import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch, ApiError } from "../api.js";
import { useAuth } from "../context/AuthContext.jsx";
import { Loading } from "../components/Ui.jsx";

// Must match the redirect_uri iam-adapter-service was told about in
// GET /sso/config, and what Keycloak was configured to redirect back to.
const REDIRECT_URI = "http://localhost:8300/sso/callback";

export default function SsoCallbackPage() {
  const navigate = useNavigate();
  const { setAuthFromSso } = useAuth();
  const [error, setError] = useState(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");

    if (!code) {
      setError("No authorization code was returned by Keycloak.");
      return undefined;
    }

    (async () => {
      try {
        const data = await apiFetch("/api/iam-adapter-service/sso/exchange", {
          method: "POST",
          body: { code, redirect_uri: REDIRECT_URI },
        });
        if (cancelled) return;
        setAuthFromSso(data);
        navigate("/dashboard", { replace: true });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 503) {
          setUnreachable(true);
        }
        setError(err.message || "SSO sign-in failed.");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [navigate, setAuthFromSso]);

  return (
    <div className="login-screen">
      <div className="login-card">
        <h1 className="login-title">Signing you in via SSO</h1>

        {!error && (
          <div className="sso-callback-loading">
            <Loading label="Exchanging authorization code with Keycloak…" />
          </div>
        )}

        {error && (
          <>
            <div className="state state-error" role="alert">
              {error}
            </div>
            {unreachable && (
              <p className="hint">
                The Keycloak identity provider looks unreachable right now (it may still be booting in this POC
                environment).
              </p>
            )}
            <p className="login-sso-back">
              <Link to="/login">← Back to passwordless sign-in</Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}

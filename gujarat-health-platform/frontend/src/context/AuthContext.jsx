import React, { createContext, useCallback, useContext, useMemo, useState } from "react";
import { apiFetch, ApiError } from "../api.js";

const STORAGE_KEY = "tp_auth";
const AuthContext = createContext(null);

function loadStoredAuth() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(loadStoredAuth);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const login = useCallback(async (username, password) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch("/api/auth-service/token", {
        method: "POST",
        body: { username, password },
      });
      const nextAuth = {
        token: data.access_token,
        tokenType: data.token_type || "bearer",
        role: data.role,
        department: data.department,
        fullName: data.full_name,
        district: data.district,
        vendorCode: data.vendor_code,
        username,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(nextAuth));
      setAuth(nextAuth);
      return nextAuth;
    } catch (e) {
      const message =
        e instanceof ApiError && e.status === 401
          ? "Invalid username or password."
          : e.message || "Login failed.";
      setError(message);
      throw e;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setAuth(null);
  }, []);

  // Used by the SSO callback page: the token exchange already happened there
  // (POST /api/iam-adapter-service/sso/exchange), this just persists the
  // resulting platform token the same way `login` does.
  const setAuthFromSso = useCallback((data) => {
    const nextAuth = {
      token: data.access_token,
      tokenType: data.token_type || "bearer",
      role: data.role,
      department: data.department,
      fullName: data.full_name,
      username: data.full_name || data.role || "sso-user",
      via: data.via || "sso",
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(nextAuth));
    setAuth(nextAuth);
    return nextAuth;
  }, []);

  const value = useMemo(
    () => ({ auth, token: auth?.token ?? null, login, logout, setAuthFromSso, loading, error, setError }),
    [auth, login, logout, setAuthFromSso, loading, error]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

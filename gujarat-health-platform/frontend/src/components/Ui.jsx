import React from "react";

export function Card({ title, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions ? <div className="card-actions">{actions}</div> : null}
        </div>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="state state-loading">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="state state-error" role="alert">
      <span>{message || "Something went wrong."}</span>
      {onRetry ? (
        <button className="btn btn-ghost btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ message = "Nothing here yet." }) {
  return <div className="state state-empty">{message}</div>;
}

export function Badge({ tone = "neutral", children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

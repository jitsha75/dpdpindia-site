"""
models.py — iam-adapter-service

Local persistence for this service is intentionally thin: the platform's
system-of-record for users/roles is auth-service (passwordless JWTs) and,
for SSO, Keycloak itself. This service keeps a best-effort log of successful
SSO exchanges (useful for support/debugging and local visibility of who
logged in via SSO), not a duplicate user table.
"""
from sqlalchemy import Column, Integer, String, DateTime

from common import Base, utcnow


class SsoLogin(Base):
    """One row per successful Keycloak -> platform-JWT exchange (POST /sso/exchange)."""
    __tablename__ = "sso_logins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=False, index=True)
    full_name = Column(String, nullable=True)
    role = Column(String, nullable=False)          # mapped platform role
    department = Column(String, nullable=True)
    realm_roles = Column(String, nullable=True)     # raw Keycloak realm_access.roles, comma-joined
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

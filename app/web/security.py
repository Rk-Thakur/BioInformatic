"""Access control (paper §3 non-functional requirements; scenario S12).

| Action                                   | lab_tech | clinician | admin |
|------------------------------------------|:--------:|:---------:|:-----:|
| see patients, variants, uploads          |    yes   |    yes    |  yes  |
| upload a VCF                             |    yes   |    yes    |  yes  |
| see alerts / recommendations / FHIR      |    no    |    yes    |  yes  |
| review alerts, edit therapy, add patient |    no    |    yes    |  yes  |
| see knowledge base and run log           |    no    |    yes    |  yes  |
"""

from functools import wraps
from urllib.parse import urlsplit

from flask import abort
from flask_login import current_user, login_required

CLINICAL = ("clinician", "admin")
ANY_ROLE = ("lab_tech", "clinician", "admin")


def roles_required(*roles):
    """Login required, and the user's role must be one of `roles` (403 otherwise)."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def safe_next(target):
    """Only allow redirects to a path on this site (no open redirect after login)."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    if response.mimetype == "text/html":
        response.headers.setdefault("Cache-Control", "no-store")  # pages contain patient data
    return response

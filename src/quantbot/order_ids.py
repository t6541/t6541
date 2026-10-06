"""Stable OKX client order identifiers."""

from __future__ import annotations

import hashlib
import re


_ASCII_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def stable_client_order_id(
    prefix: str,
    identity: str,
    suffix: str = "",
    *,
    reserve: int = 0,
) -> str:
    """Build a deterministic ASCII-alphanumeric OKX id without losing uniqueness.

    ``reserve`` leaves room for a related algo/protection suffix such as ``T``
    or ``P``.  The digest covers the original, unmodified identity so event ids
    which differ only by punctuation cannot collapse to the same order id.
    """
    if not 0 <= reserve < 32:
        raise ValueError("reserve must leave at least one client order id character")
    if not prefix or not prefix.isascii() or not prefix.isalnum():
        raise ValueError("prefix must contain only ASCII letters and digits")
    if suffix and (not suffix.isascii() or not suffix.isalnum()):
        raise ValueError("suffix must contain only ASCII letters and digits")

    raw_identity = str(identity)
    digest = hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()[:8].upper()
    cleaned = _ASCII_ALNUM.sub("", raw_identity)
    limit = 32 - reserve
    available = limit - len(prefix) - len(digest) - len(suffix)
    if available < 0:
        raise ValueError("prefix and suffix leave no room for a stable digest")
    client_id = prefix + cleaned[:available] + digest + suffix
    if not client_id or len(client_id) > limit or not client_id.isascii() or not client_id.isalnum():
        raise ValueError("generated client order id is invalid")
    return client_id


def related_client_order_id(client_order_id: str, suffix: str) -> str:
    """Create a valid related algo/protection id from a valid entry id."""
    if (not client_order_id or len(client_order_id) > 32
            or not client_order_id.isascii() or not client_order_id.isalnum()):
        raise ValueError("client_order_id must be 1-32 ASCII alphanumeric characters")
    if len(suffix) != 1 or not suffix.isascii() or not suffix.isalnum():
        raise ValueError("related order suffix must be one ASCII alphanumeric character")
    return client_order_id[:31] + suffix

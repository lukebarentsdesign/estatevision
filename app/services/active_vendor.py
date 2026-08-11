"""Which vendor is active per integration category (avatar, tts, etc.).

Categories with only one registered vendor need no explicit choice stored --
`get_active_vendor` falls back to the first `IntegrationDefinition` with that
`category_key`, so existing single-vendor categories work unchanged.
"""

from __future__ import annotations

from sqlmodel import Session, select

from ..models import ActiveVendorChoice
from .integration_registry import list_integrations


def _vendors_in_category(category_key: str) -> list[str]:
    slugs = [d.slug for d in list_integrations() if d.category_key == category_key]
    if not slugs:
        raise ValueError(f"Unknown category_key {category_key!r}")
    return slugs


def get_active_vendor(session: Session, category_key: str) -> str:
    """Returns the slug of the active vendor for a category.

    Falls back to the first-registered IntegrationDefinition with that
    category_key if no explicit choice has been stored.
    """
    vendors = _vendors_in_category(category_key)

    stmt = select(ActiveVendorChoice).where(ActiveVendorChoice.category_key == category_key)
    row = session.exec(stmt).first()
    if row is not None and row.vendor_slug in vendors:
        return row.vendor_slug
    return vendors[0]


def set_active_vendor(session: Session, category_key: str, vendor_slug: str) -> None:
    """Stores the active vendor choice. Validates that `vendor_slug` is a
    registered integration belonging to `category_key`."""
    vendors = _vendors_in_category(category_key)
    if vendor_slug not in vendors:
        raise ValueError(
            f"{vendor_slug!r} is not a registered vendor in category {category_key!r} "
            f"(known vendors: {vendors})"
        )

    stmt = select(ActiveVendorChoice).where(ActiveVendorChoice.category_key == category_key)
    row = session.exec(stmt).first()
    if row is None:
        row = ActiveVendorChoice(category_key=category_key, vendor_slug=vendor_slug)
    else:
        row.vendor_slug = vendor_slug
    session.add(row)
    session.commit()

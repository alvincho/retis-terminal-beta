"""
Public package exports for `prompits.channels`.

Prompits is the base framework layer for FinMAS. The channels package exposes the
small, generic delivery-lane helpers that higher layers can use for notification
providers and destination publishing status.
"""

from prompits.channels.runtime import (
    B2B_CHANNEL_KINDS,
    build_delivery_snapshot,
    default_b2b_channels,
    normalize_channel_lane,
)
from prompits.channels.notifications import (
    NOTIFICATION_PROVIDER_KINDS,
    NotificationDispatcher,
    extract_notification_config,
    normalize_notification_providers,
    scrub_notification_event,
    store_forward_summary_from_card,
)


__all__ = [
    "B2B_CHANNEL_KINDS",
    "NOTIFICATION_PROVIDER_KINDS",
    "build_delivery_snapshot",
    "default_b2b_channels",
    "NotificationDispatcher",
    "extract_notification_config",
    "normalize_channel_lane",
    "normalize_notification_providers",
    "scrub_notification_event",
    "store_forward_summary_from_card",
]

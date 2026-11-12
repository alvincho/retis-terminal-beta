"""Validated presentation preferences; deliberately excluded from execution policy."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: app_consultant. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
import re
from fastapi import HTTPException


def validate_market_appearance(value):
    allowed={'upColor','downColor','colorChanges','flashEnabled','flashColor','flashDurationMs'}
    if not isinstance(value,dict) or set(value)-allowed:raise HTTPException(422,'Invalid market appearance settings.')
    for key in ('upColor','downColor','flashColor'):
        if key in value and (not isinstance(value[key],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',value[key])):raise HTTPException(422,'Choose a valid market color.')
    for key in ('colorChanges','flashEnabled'):
        if key in value and type(value[key]) is not bool:raise HTTPException(422,'Market appearance toggles must be boolean.')
    if 'flashDurationMs' in value and (type(value['flashDurationMs']) is not int or not 50<=value['flashDurationMs']<=5000):raise HTTPException(422,'Highlight duration must be 0.05–5 seconds.')


def validate_display_timezone(value, *, allow_modes=True):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    if allow_modes and value in ('system','market'):return
    if not isinstance(value,str) or len(value)>100:raise HTTPException(422,'Choose a valid display timezone.')
    try:ZoneInfo(value)
    except (ZoneInfoNotFoundError,ValueError):raise HTTPException(422,'Choose a valid display timezone.') from None

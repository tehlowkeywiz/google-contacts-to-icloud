#!/usr/bin/env python3
"""
merge_contacts.py — Google Contacts CSV + vCard → clean Apple Contacts vCard

Keeps only contacts that have a phone number, merges duplicates by phone across
all sources, and maps each contact to native iPhone / Apple Contacts fields.
Produces a clean Apple-native import: every field with a native Apple target
is kept (name, nickname, company, job title, phones with label fidelity, emails,
addresses, birthday, notes, and embedded iCloud photos); Google-specific data
(websites, social profiles, group labels, custom fields, photo URLs, relations)
is dropped. Company-only contacts (org, no person name) are flagged
X-ABShowAs:COMPANY so they file under the company name.

To keep or drop a different set of fields, fork the script and adapt
render_vcard() — the field logic lives in that one function.

Usage:
    python3 merge_contacts.py [--input DIR] [--output FILE] [--merge-by-name]
"""

import argparse
import csv
import re
import sys
from pathlib import Path

__version__ = '1.0.0'


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

def normalize_phone(raw: str) -> str:
    """Return digits-only form; strip leading 1 from 11-digit NANP numbers.

    Args:
        raw: Raw phone string, may contain formatting characters.

    Returns:
        Digits-only string, 10 digits for standard North American numbers.
    """
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


def _dedupe_phones_prefer_plus(
    phones: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """De-duplicate phones by normalized form, preferring a '+' (E.164) raw.

    When the same number appears in more than one format across merged sources
    (e.g. '+1 (514) 555-1234' and '514-555-1234' — equal after normalization),
    the international '+'-prefixed form is kept so the number dials correctly
    worldwide. No number is rewritten; only the existing representation is
    chosen. First-seen order and label are preserved.

    Args:
        phones: List of (normalized, raw, label) phone tuples.

    Returns:
        List of (normalized, raw, label) tuples, one per normalized number.
    """
    by_norm: dict[str, tuple[str, str]] = {}  # norm -> (raw, label)
    order: list[str] = []
    for norm, raw, label in phones:
        if norm not in by_norm:
            by_norm[norm] = (raw, label)
            order.append(norm)
        else:
            existing_raw, existing_label = by_norm[norm]
            # Prefer a '+'-prefixed (international) raw over a bare one.
            if raw.strip().startswith('+') and not existing_raw.strip().startswith('+'):
                by_norm[norm] = (raw, existing_label)
    return [(norm, by_norm[norm][0], by_norm[norm][1]) for norm in order]


# ---------------------------------------------------------------------------
# vCard value escaping / unescaping
# ---------------------------------------------------------------------------

def vcard_escape(value: str) -> str:
    """Escape special characters per vCard 3.0 spec.

    Args:
        value: Plain text string to escape.

    Returns:
        Escaped string safe for inclusion in a vCard property value.
    """
    value = value.replace('\x00', '')
    value = value.replace('\\', '\\\\')
    value = value.replace(';', '\\;')
    value = value.replace(',', '\\,')
    value = value.replace('\n', '\\n')
    value = value.replace('\r', '')
    return value


def vcard_unescape(value: str) -> str:
    """Unescape a vCard property value.

    Handles the standard escape sequences: \\\\, \\;, \\,, \\n, \\N.

    Args:
        value: Raw vCard property value, possibly containing escape sequences.

    Returns:
        Plain text string with escape sequences resolved.
    """
    value = value.replace('\\n', '\n').replace('\\N', '\n')
    value = value.replace('\\,', ',')
    value = value.replace('\\;', ';')
    value = value.replace('\\\\', '\\')
    return value


# ---------------------------------------------------------------------------
# Phone label → vCard TYPE / item-group rendering
# ---------------------------------------------------------------------------

# Standard labels that map to simple vCard TYPE values (no item-group needed).
# Checked case-insensitively after stripping "* " Google prefix.
_STANDARD_PHONE_TYPES: dict[str, str] = {
    'mobile': 'CELL',
    'cell':   'CELL',
    'home':   'HOME',
    'work':   'WORK',
    'office': 'WORK',
    'main':   'MAIN',
    'pager':  'PAGER',
    'other':  'OTHER',
    'fax':    'FAX',
}

# These standard labels map to multi-value TYPE (e.g. HOME,FAX).
_FAX_TYPES: dict[str, str] = {
    'home fax':  'HOME,FAX',
    'work fax':  'WORK,FAX',
    'homefax':   'HOME,FAX',
    'workfax':   'WORK,FAX',
}


def phone_label_to_type(label: str) -> str:
    """Map a phone label string to a standard vCard TYPE value, or '' for custom.

    Standard labels return a TYPE string (e.g. 'CELL', 'HOME', 'WORK,FAX').
    iPhone and non-standard/custom labels return '' to signal that an
    item-group with X-ABLabel should be used instead.

    Args:
        label: Label from Google CSV or vCard TYPE param (e.g. 'Mobile', 'iPhone').

    Returns:
        vCard TYPE string, or '' if the label is custom/non-standard.
    """
    normalized = label.lower().strip().lstrip('* ').strip()

    # Unlabeled / empty → default to CELL
    if not normalized:
        return 'CELL'

    # Fax combinations first (exact match; longer keys beat single-word overlap)
    if normalized in _FAX_TYPES:
        return _FAX_TYPES[normalized]

    # Standard single-type labels (exact match)
    if normalized in _STANDARD_PHONE_TYPES:
        return _STANDARD_PHONE_TYPES[normalized]

    # Custom / non-standard (e.g. iPhone, Service, Telegram, "mobile home", etc.) → item-group
    return ''


def _is_custom_phone_label(label: str) -> bool:
    """Return True if this label requires an item-group + X-ABLabel rendering.

    Args:
        label: Raw phone label string.

    Returns:
        True when phone_label_to_type() returns '' for this label.
    """
    return phone_label_to_type(label) == ''


# ---------------------------------------------------------------------------
# Social-profile detection helpers
# ---------------------------------------------------------------------------

_SOCIAL_URL_KEYWORDS = (
    'facebook.com', 'linkedin.com', 'twitter.com', 'instagram.com',
    'x.com', 'angellist.com', 'angel.co', 'plus.google.com',
    'youtube.com', 'tiktok.com',
)

_SOCIAL_LABEL_KEYWORDS = (
    'facebook', 'linkedin', 'twitter', 'instagram', 'angellist',
    'angel list', 'google+', 'social', 'x.com', 'youtube',
    'tiktok',
)


def _is_social_url(url: str) -> bool:
    """Return True if the URL looks like a social-network profile link."""
    lower = url.lower()
    return any(kw in lower for kw in _SOCIAL_URL_KEYWORDS)


def _is_social_label(label: str) -> bool:
    """Return True if the label names a social network or generic profile."""
    lower = label.lower()
    return any(kw in lower for kw in _SOCIAL_LABEL_KEYWORDS)


# ---------------------------------------------------------------------------
# Sanitize arbitrary text for use in an X-GOOGLE- property name
# ---------------------------------------------------------------------------

def _sanitize_property_name_segment(text: str) -> str:
    """Convert arbitrary label text to a safe vCard property-name segment.

    Replaces characters not in [A-Za-z0-9-] with hyphens, then collapses
    consecutive hyphens and strips leading/trailing ones.

    Args:
        text: Arbitrary label string (e.g. a Google Custom Field label).

    Returns:
        Sanitized uppercase string usable as a vCard X-GOOGLE-* suffix.
    """
    sanitized = re.sub(r'[^A-Za-z0-9-]', '-', text)
    sanitized = re.sub(r'-{2,}', '-', sanitized)
    sanitized = sanitized.strip('-').upper()
    return sanitized or 'FIELD'


# ---------------------------------------------------------------------------
# Shared contact dict type
# ---------------------------------------------------------------------------
# A contact is a plain dict.  Core keys (always present):
#   first, middle, last, display, org       — strings
#   phones   — list of (normalized_digits, raw_string, label_string)
#              label_string is the ORIGINAL label (e.g. 'iPhone', 'Work Fax')
#   emails   — list of (address_string, label_string)
#
# Extended keys (populated from Google CSV, absent for minimal iCloud passhtrough):
#   prefix, suffix, nickname                 — strings
#   title, department                        — strings (org title/dept)
#   bday                                     — string (BDAY value, raw)
#   notes                                    — string
#   photo_url                                — string (Google's photo URL, if any)
#   labels                                   — list of str (group membership)
#   addresses — list of dict with keys:
#       label, formatted, street, city, pobox, region, postal, country, extended
#   urls     — list of (label_string, url_string)
#   relations — list of (label_string, value_string)
#   custom_fields — list of (label_string, value_string)
#   extra_vcards  — list of raw vCard property lines (for passthrough)
#
# Internal keys set by merge_contacts():
#   phone_norms     — set[str]
#   _merged_count   — int
#   _raw_vcard      — str (original vCard block for iCloud passthrough)
#   _source         — 'google' | 'icloud' | 'merged'

def _empty_contact() -> dict:
    return {
        'first': '', 'middle': '', 'last': '',
        'display': '', 'org': '',
        'phones': [],
        'emails': [],
        # extended
        'prefix': '', 'suffix': '', 'nickname': '',
        'title': '', 'department': '',
        'bday': '', 'notes': '', 'photo_url': '',
        'labels': [],
        'addresses': [],
        'urls': [],
        'relations': [],
        'custom_fields': [],
        'extra_vcards': [],
        '_raw_vcard': '',
        '_source': 'unknown',
    }


# ---------------------------------------------------------------------------
# Google CSV parsing
# ---------------------------------------------------------------------------

def split_multi(value: str) -> list[str]:
    """Split a Google Contacts multi-value cell on ' ::: '.

    Args:
        value: Raw cell value, possibly containing ' ::: ' separators.

    Returns:
        List of non-empty stripped substrings.
    """
    return [v.strip() for v in value.split(' ::: ') if v.strip()]


def _row_str(row: dict, key: str, default: str = '') -> str:
    """Safely retrieve a string value from a DictReader row.

    csv.DictReader sets missing trailing-column cells to None, so a plain
    ``row.get(key, '')`` can return None and crash on ``.strip()``.

    Args:
        row: DictReader row dict.
        key: Column name to look up.
        default: Fallback string when the key is absent or the value is None.

    Returns:
        Stripped string value, never None.
    """
    val = row.get(key, default)
    return (val or default).strip()


def parse_csv_row(row: dict) -> dict | None:
    """Parse one Google Contacts CSV row into a contact dict.

    Maps every Google CSV field to its vCard equivalent (lossless).
    Returns None only if the row has no real phone number (phone-only filter).

    Args:
        row: DictReader row from a Google Contacts CSV export.

    Returns:
        Contact dict, or None if the row has no real phone number.
    """
    # --- Phones (must have at least one to keep the contact) ---
    phones: list[tuple[str, str, str]] = []
    for i in range(1, 20):
        label_col = f'Phone {i} - Label'
        value_col = f'Phone {i} - Value'
        if value_col not in row:
            break
        raw_values = split_multi(_row_str(row, value_col))
        raw_labels = split_multi(_row_str(row, label_col))
        for j, raw in enumerate(raw_values):
            if not raw:
                continue
            norm = normalize_phone(raw)
            if len(norm) < 7:
                continue
            label = raw_labels[j] if j < len(raw_labels) else ''
            phones.append((norm, raw, label))

    if not phones:
        return None  # email-only — drop

    # --- Name fields ---
    first    = _row_str(row, 'First Name')
    middle   = _row_str(row, 'Middle Name')
    last     = _row_str(row, 'Last Name')
    prefix   = _row_str(row, 'Name Prefix')
    suffix   = _row_str(row, 'Name Suffix')
    nickname = _row_str(row, 'Nickname')
    org      = _row_str(row, 'Organization Name')
    title    = _row_str(row, 'Organization Title')
    dept     = _row_str(row, 'Organization Department')

    parts = [p for p in [first, middle, last] if p]
    if parts:
        display_name = ' '.join(parts)
    elif org:
        display_name = org
    else:
        email1 = _row_str(row, 'E-mail 1 - Value')
        display_name = email1.split('@')[0] if email1 else 'Unknown'

    # --- Emails ---
    emails: list[tuple[str, str]] = []
    for i in range(1, 20):
        label_col = f'E-mail {i} - Label'
        value_col = f'E-mail {i} - Value'
        if value_col not in row:
            break
        label = _row_str(row, label_col)
        for addr in split_multi(_row_str(row, value_col)):
            if addr and not any(e[0] == addr for e in emails):
                emails.append((addr, label))

    # --- Addresses ---
    addresses: list[dict] = []
    for i in range(1, 10):
        lbl_col = f'Address {i} - Label'
        if lbl_col not in row:
            break
        street    = _row_str(row, f'Address {i} - Street')
        city      = _row_str(row, f'Address {i} - City')
        pobox     = _row_str(row, f'Address {i} - PO Box')
        region    = _row_str(row, f'Address {i} - Region')
        postal    = _row_str(row, f'Address {i} - Postal Code')
        country   = _row_str(row, f'Address {i} - Country')
        extended  = _row_str(row, f'Address {i} - Extended Address')
        formatted = _row_str(row, f'Address {i} - Formatted')
        label     = _row_str(row, lbl_col)
        # Only add if at least one component is non-empty
        if any([street, city, pobox, region, postal, country, extended, formatted]):
            addresses.append({
                'label': label,
                'formatted': formatted,
                'street': street,
                'city': city,
                'pobox': pobox,
                'region': region,
                'postal': postal,
                'country': country,
                'extended': extended,
            })

    # --- Websites / URLs ---
    urls: list[tuple[str, str]] = []
    for i in range(1, 10):
        label_col = f'Website {i} - Label'
        value_col = f'Website {i} - Value'
        if value_col not in row:
            break
        label = _row_str(row, label_col)
        url   = _row_str(row, value_col)
        if url:
            urls.append((label, url))

    # --- Relations ---
    relations: list[tuple[str, str]] = []
    for i in range(1, 10):
        label_col = f'Relation {i} - Label'
        value_col = f'Relation {i} - Value'
        if value_col not in row:
            break
        label = _row_str(row, label_col)
        value = _row_str(row, value_col)
        if value:
            relations.append((label, value))

    # --- Custom fields ---
    custom_fields: list[tuple[str, str]] = []
    for i in range(1, 10):
        label_col = f'Custom Field {i} - Label'
        value_col = f'Custom Field {i} - Value'
        if value_col not in row:
            break
        label = _row_str(row, label_col)
        value = _row_str(row, value_col)
        if value:
            custom_fields.append((label or f'Field{i}', value))

    # --- Misc scalar fields ---
    bday      = _row_str(row, 'Birthday').replace('\r', '').replace('\n', '')
    notes     = _row_str(row, 'Notes')
    photo_url = _row_str(row, 'Photo')

    # --- Labels (group membership) ---
    labels_raw = _row_str(row, 'Labels')
    groups = [g.strip() for g in labels_raw.split(' ::: ') if g.strip()] if labels_raw else []
    # Remove the near-universal * myContacts from CATEGORIES (it's noise, not a real group)
    groups = [g.lstrip('* ') for g in groups if g.lstrip('* ') not in ('myContacts',)]

    # --- Unknown / extra columns — preserved as X-GOOGLE-<column> ---
    known_columns: set[str] = {
        'First Name', 'Middle Name', 'Last Name',
        'Phonetic First Name', 'Phonetic Middle Name', 'Phonetic Last Name',
        'Name Prefix', 'Name Suffix', 'Nickname', 'File As',
        'Organization Name', 'Organization Title', 'Organization Department',
        'Birthday', 'Notes', 'Photo', 'Labels',
    }
    # Dynamically add all indexed multi-value column names we already handle
    for i in range(1, 20):
        known_columns.update({
            f'Phone {i} - Label', f'Phone {i} - Value',
            f'E-mail {i} - Label', f'E-mail {i} - Value',
            f'Address {i} - Label', f'Address {i} - Formatted',
            f'Address {i} - Street', f'Address {i} - City',
            f'Address {i} - PO Box', f'Address {i} - Region',
            f'Address {i} - Postal Code', f'Address {i} - Country',
            f'Address {i} - Extended Address',
            f'Website {i} - Label', f'Website {i} - Value',
            f'Relation {i} - Label', f'Relation {i} - Value',
            f'Custom Field {i} - Label', f'Custom Field {i} - Value',
        })

    extra_vcards: list[str] = []
    for col, val in row.items():
        val_str = (val or '').strip()
        if col in known_columns or not val_str:
            continue
        seg = _sanitize_property_name_segment(col)
        extra_vcards.append(f'X-GOOGLE-{seg}:{vcard_escape(val_str)}')

    contact = _empty_contact()
    contact.update({
        'first': first, 'middle': middle, 'last': last,
        'display': display_name, 'org': org,
        'prefix': prefix, 'suffix': suffix, 'nickname': nickname,
        'title': title, 'department': dept,
        'phones': phones, 'emails': emails,
        'bday': bday, 'notes': notes, 'photo_url': photo_url,
        'labels': groups,
        'addresses': addresses,
        'urls': urls,
        'relations': relations,
        'custom_fields': custom_fields,
        'extra_vcards': extra_vcards,
        '_source': 'google',
    })
    return contact


def read_csv_file(path: Path) -> tuple[list[dict], int, int]:
    """Read a Google Contacts CSV file and return parsed contacts.

    Args:
        path: Path to a Google Contacts CSV export file.

    Returns:
        Tuple of (contacts, phone_bearing_count, email_only_dropped_count).
    """
    contacts: list[dict] = []
    phone_bearing = 0
    email_only = 0
    with open(path, encoding='utf-8-sig', newline='') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            contact = parse_csv_row(row)
            if contact is None:
                email_only += 1
            else:
                phone_bearing += 1
                contacts.append(contact)
    return contacts, phone_bearing, email_only


# ---------------------------------------------------------------------------
# vCard input parsing
# ---------------------------------------------------------------------------

def _unfold_vcard_lines(raw_text: str) -> list[str]:
    """Unfold vCard line continuations (lines beginning with space or tab).

    Per RFC 6350 §3.2, a long logical line may be split across multiple
    physical lines, with each continuation line beginning with a single
    space or tab character.

    Args:
        raw_text: Raw vCard file content.

    Returns:
        List of unfolded logical lines.
    """
    logical: list[str] = []
    for physical in raw_text.splitlines():
        if physical and physical[0] in (' ', '\t'):
            # Continuation — append to previous logical line, strip leading space
            if logical:
                logical[-1] += physical[1:]
        else:
            logical.append(physical)
    return logical


def _parse_vcard_property(line: str) -> tuple[str, dict[str, list[str]], str, str]:
    """Split a vCard property line into (name, params, value, group).

    Handles the form: [group.]NAME;PARAM=val1,val2;PARAM2=val:property value

    Args:
        line: A single unfolded vCard property line.

    Returns:
        Tuple of (property_name_upper, params_dict, value_string, group_prefix).
        params_dict maps upper-cased param names to lists of upper-cased values.
        group_prefix is the raw group string before the dot (e.g. 'item1'), or ''.
    """
    colon_pos = line.find(':')
    if colon_pos < 0:
        return line.upper(), {}, '', ''

    prop_part = line[:colon_pos]
    value = line[colon_pos + 1:]

    segments = prop_part.split(';')
    raw_name = segments[0]

    # Extract group prefix (e.g. 'item1' from 'item1.TEL')
    group_prefix = ''
    if '.' in raw_name:
        group_prefix, _, raw_name = raw_name.partition('.')

    name = raw_name.upper()

    params: dict[str, list[str]] = {}
    for seg in segments[1:]:
        if '=' in seg:
            pname, _, pvals = seg.partition('=')
            params.setdefault(pname.upper(), []).extend(
                v.upper() for v in pvals.split(',')
            )
        else:
            # Bare param (e.g. 'WORK', 'CELL') — treated as TYPE value
            params.setdefault('TYPE', []).append(seg.upper())

    return name, params, value, group_prefix


def parse_vcf_file(path: Path) -> tuple[list[dict], int, int]:
    """Parse a vCard file (3.0 or 4.0) and return contacts with phones.

    iCloud vCards are stored with their original raw block so they can be
    passed through verbatim when they are NOT merged with a Google contact.

    Args:
        path: Path to a .vcf file.

    Returns:
        Tuple of (contacts, phone_bearing_count, email_only_dropped_count).
    """
    raw_text = path.read_text(encoding='utf-8-sig', errors='replace')
    lines = _unfold_vcard_lines(raw_text)

    contacts: list[dict] = []
    phone_bearing = 0
    email_only = 0

    current: dict | None = None
    current_raw_lines: list[str] = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        upper = line_stripped.upper()

        if upper == 'BEGIN:VCARD':
            current = _empty_contact()
            current['_source'] = 'icloud'
            current_raw_lines = [line_stripped]
            continue

        if upper == 'END:VCARD':
            if current is not None:
                current_raw_lines.append(line_stripped)
                current['_raw_vcard'] = '\r\n'.join(current_raw_lines) + '\r\n'
                if current['phones']:
                    phone_bearing += 1
                    contacts.append(current)
                else:
                    email_only += 1
            current = None
            current_raw_lines = []
            continue

        if current is None:
            continue

        current_raw_lines.append(line_stripped)

        prop, params, value, group = _parse_vcard_property(line_stripped)
        value_unescaped = vcard_unescape(value)

        if prop == 'FN':
            current['display'] = value_unescaped.strip()

        elif prop == 'N':
            parts = value_unescaped.split(';')
            current['last']   = parts[0].strip() if len(parts) > 0 else ''
            current['first']  = parts[1].strip() if len(parts) > 1 else ''
            current['middle'] = parts[2].strip() if len(parts) > 2 else ''
            if not current['display']:
                name_parts = [p for p in [current['first'], current['middle'], current['last']] if p]
                current['display'] = ' '.join(name_parts)

        elif prop == 'ORG':
            parts = value_unescaped.split(';')
            current['org']        = parts[0].strip()
            current['department'] = parts[1].strip() if len(parts) > 1 else ''

        elif prop == 'TITLE':
            current['title'] = value_unescaped.strip()

        elif prop == 'NICKNAME':
            current['nickname'] = value_unescaped.strip()

        elif prop == 'NOTE':
            current['notes'] = value_unescaped.strip()

        elif prop == 'BDAY':
            current['bday'] = value.strip().replace('\r', '').replace('\n', '')

        elif prop == 'TEL':
            raw = value.strip()
            if raw.lower().startswith('tel:'):
                raw = raw[4:].strip()
            if not raw:
                continue
            norm = normalize_phone(raw)
            if len(norm) < 7:
                continue
            # Derive original label from TYPE params for passthrough fidelity
            type_vals = params.get('TYPE', [])
            label = ','.join(type_vals)  # e.g. 'CELL' or 'WORK,FAX'
            existing_norms = {p[0] for p in current['phones']}
            if norm not in existing_norms:
                current['phones'].append((norm, raw, label))

        elif prop == 'EMAIL':
            addr = value.strip()
            if addr.lower().startswith('mailto:'):
                addr = addr[7:].strip()
            label = ','.join(params.get('TYPE', []))
            if addr and not any(e[0] == addr for e in current['emails']):
                current['emails'].append((addr, label))

        elif prop == 'ADR':
            # ADR:pobox;extended;street;city;region;postal;country
            parts = value_unescaped.split(';')
            label = ','.join(params.get('TYPE', []))
            current['addresses'].append({
                'label': label,
                'formatted': '',
                'pobox':    parts[0].strip() if len(parts) > 0 else '',
                'extended': parts[1].strip() if len(parts) > 1 else '',
                'street':   parts[2].strip() if len(parts) > 2 else '',
                'city':     parts[3].strip() if len(parts) > 3 else '',
                'region':   parts[4].strip() if len(parts) > 4 else '',
                'postal':   parts[5].strip() if len(parts) > 5 else '',
                'country':  parts[6].strip() if len(parts) > 6 else '',
            })

        elif prop == 'URL':
            label = ','.join(params.get('TYPE', []))
            current['urls'].append((label, value.strip()))

        elif prop == 'CATEGORIES':
            current['labels'].extend(c.strip() for c in value_unescaped.split(',') if c.strip())

    return contacts, phone_bearing, email_only


# ---------------------------------------------------------------------------
# Dedup / merge logic
# ---------------------------------------------------------------------------

def best_name(a: str, b: str) -> str:
    """Return the longer / more complete non-empty name.

    Args:
        a: First candidate name string.
        b: Second candidate name string.

    Returns:
        The longer of the two strings; if one is empty, returns the other.
    """
    if not a:
        return b
    if not b:
        return a
    return a if len(a) >= len(b) else b


def _merge_two_contacts(base: dict, incoming: dict) -> None:
    """Merge incoming fields into base (mutates base in-place).

    Union strategy for multi-valued fields; single-valued fields prefer
    the existing (base) value unless it is empty.

    Args:
        base: The canonical card to merge into (mutated).
        incoming: The card being folded in.
    """
    base['display'] = best_name(base['display'], incoming['display'])
    base['first']   = best_name(base['first'],   incoming['first'])
    base['middle']  = best_name(base['middle'],  incoming['middle'])
    base['last']    = best_name(base['last'],     incoming['last'])
    base['org']     = best_name(base['org'],      incoming['org'])

    for field in ('prefix', 'suffix', 'nickname', 'title', 'department',
                  'bday', 'notes', 'photo_url'):
        if not base.get(field):
            base[field] = incoming.get(field, '')

    # Union phones
    existing_norms: set[str] = base.get('phone_norms', set())
    for norm, raw, label in incoming['phones']:
        if norm not in existing_norms:
            base['phones'].append((norm, raw, label))
            existing_norms.add(norm)
    base['phone_norms'] = existing_norms

    # Union emails (by address)
    existing_addrs = {e[0] for e in base['emails']}
    for addr, label in incoming['emails']:
        if addr not in existing_addrs:
            base['emails'].append((addr, label))
            existing_addrs.add(addr)

    # Union addresses (by street+city+postal key; postal prevents PO-box/partial collisions)
    def _addr_key(a: dict) -> str:
        return (a.get('street', '') + '|' + a.get('city', '') + '|' + a.get('postal', '')).lower()

    existing_addr_keys = {_addr_key(a) for a in base['addresses']}
    for addr in incoming['addresses']:
        key = _addr_key(addr)
        if key and key not in existing_addr_keys:
            base['addresses'].append(addr)
            existing_addr_keys.add(key)

    # Union URLs (by url value)
    existing_urls = {u[1] for u in base['urls']}
    for label, url in incoming['urls']:
        if url not in existing_urls:
            base['urls'].append((label, url))
            existing_urls.add(url)

    # Union labels
    for lbl in incoming['labels']:
        if lbl not in base['labels']:
            base['labels'].append(lbl)

    # Union relations
    existing_rels = {(r[0], r[1]) for r in base['relations']}
    for rel in incoming['relations']:
        if (rel[0], rel[1]) not in existing_rels:
            base['relations'].append(rel)
            existing_rels.add((rel[0], rel[1]))

    # Union custom fields
    existing_cf = {(c[0], c[1]) for c in base['custom_fields']}
    for cf in incoming['custom_fields']:
        if (cf[0], cf[1]) not in existing_cf:
            base['custom_fields'].append(cf)
            existing_cf.add((cf[0], cf[1]))

    # Union extra_vcards lines
    existing_ev = set(base['extra_vcards'])
    for ev in incoming.get('extra_vcards', []):
        if ev not in existing_ev:
            base['extra_vcards'].append(ev)
            existing_ev.add(ev)

    base['_merged_count'] = base.get('_merged_count', 0) + 1
    # If the incoming is iCloud and base is not, carry the raw block for reference
    if incoming.get('_source') == 'icloud' and not base.get('_raw_vcard'):
        base['_raw_vcard'] = incoming.get('_raw_vcard', '')
    # Mark as merged
    base['_source'] = 'merged'


def merge_contacts(contacts: list[dict]) -> list[dict]:
    """Merge contacts sharing any normalized phone number into single cards.

    Uses a greedy linear scan: when a new contact shares any normalized phone
    with an already-seen card, it is merged into that card (union of all
    multi-valued fields, single-valued prefer existing). Operates in a single
    pass, so contacts that could transitively merge via a third contact will
    merge only if they directly share a number with the canonical card already
    seen. This is sufficient for the practical dedup case.

    Args:
        contacts: List of parsed contact dicts.

    Returns:
        List of merged contact dicts, each with an added '_merged_count' int
        and 'phone_norms' set for downstream use.
    """
    phone_to_idx: dict[str, int] = {}
    merged: list[dict] = []

    for contact in contacts:
        target_idx: int | None = None
        for norm, _raw, _label in contact['phones']:
            if norm in phone_to_idx:
                target_idx = phone_to_idx[norm]
                break

        if target_idx is None:
            idx = len(merged)
            card = dict(contact)
            card['phone_norms'] = {n for n, _, _ in contact['phones']}
            card['_merged_count'] = 0
            merged.append(card)
            for norm, _raw, _label in contact['phones']:
                phone_to_idx[norm] = idx
        else:
            card = merged[target_idx]
            _merge_two_contacts(card, contact)
            for norm, _raw, _label in contact['phones']:
                if norm not in card['phone_norms']:
                    card['phone_norms'].add(norm)
                    phone_to_idx[norm] = target_idx

    return merged


def merge_by_name_pass(contacts: list[dict]) -> list[dict]:
    """Optional second-pass merge on identical display names.

    Higher false-positive risk (two different people named 'John Smith'
    would be collapsed). Off by default.

    Args:
        contacts: List of merged contact dicts from merge_contacts().

    Returns:
        Further-collapsed list with same-named cards combined.
    """
    name_to_idx: dict[str, int] = {}
    merged: list[dict] = []

    for card in contacts:
        name_key = card['display'].strip().lower()
        if name_key and name_key in name_to_idx:
            target = merged[name_to_idx[name_key]]
            _merge_two_contacts(target, card)
        else:
            idx = len(merged)
            merged.append(card)
            if name_key:
                name_to_idx[name_key] = idx

    return merged


# ---------------------------------------------------------------------------
# vCard 3.0 output rendering
# ---------------------------------------------------------------------------

def _render_phone_lines(phones: list[tuple[str, str, str]]) -> list[str]:
    """Render a list of (norm, raw, label) tuples to TEL property lines.

    Standard labels → TEL;TYPE=<TYPE>:<raw>
    Custom / iPhone labels → item-group pair:
        itemN.TEL;type=CELL:<raw>
        itemN.X-ABLabel:<original label>

    Args:
        phones: List of (normalized, raw, label) phone tuples.

    Returns:
        List of vCard property strings (no CRLF).
    """
    lines: list[str] = []
    item_counter = 1
    for _norm, raw, label in phones:
        vtype = phone_label_to_type(label)
        if vtype:
            # Standard label — simple TYPE
            lines.append(f'TEL;TYPE={vtype}:{vcard_escape(raw)}')
        else:
            # Custom label — item-group with X-ABLabel
            prefix = f'item{item_counter}'
            item_counter += 1
            lines.append(f'{prefix}.TEL;type=CELL:{vcard_escape(raw)}')
            lines.append(f'{prefix}.X-ABLabel:{vcard_escape(label)}')
    return lines


def _render_email_lines(emails: list[tuple[str, str]]) -> list[str]:
    """Render email tuples to EMAIL property lines.

    Args:
        emails: List of (address, label) tuples.

    Returns:
        List of vCard property strings (no CRLF).
    """
    lines: list[str] = []
    item_counter = 100  # keep item numbers distinct from phone item groups
    for addr, label in emails:
        lower_label = label.lower().strip()
        if lower_label in ('', 'internet', 'home', 'work', 'other'):
            type_val = label.upper() if label.upper() in ('HOME', 'WORK', 'OTHER') else 'INTERNET'
            lines.append(f'EMAIL;TYPE={type_val}:{vcard_escape(addr)}')
        else:
            # Custom email label
            prefix = f'item{item_counter}'
            item_counter += 1
            lines.append(f'{prefix}.EMAIL:{vcard_escape(addr)}')
            lines.append(f'{prefix}.X-ABLabel:{vcard_escape(label)}')
    return lines


def _render_address_lines(addresses: list[dict]) -> list[str]:
    """Render address dicts to ADR property lines.

    Args:
        addresses: List of address dicts with structured fields.

    Returns:
        List of vCard property strings (no CRLF).
    """
    lines: list[str] = []
    item_counter = 200
    for addr in addresses:
        label = addr.get('label', '').strip().upper()
        # Map common Google labels to vCard TYPE
        if label in ('HOME', 'WORK', 'OTHER'):
            type_param = f';TYPE={label}'
        elif label:
            type_param = ''
            # Will add X-ABLabel via item group
        else:
            type_param = ''

        adr_value = (
            f'{vcard_escape(addr.get("pobox", ""))};'
            f'{vcard_escape(addr.get("extended", ""))};'
            f'{vcard_escape(addr.get("street", ""))};'
            f'{vcard_escape(addr.get("city", ""))};'
            f'{vcard_escape(addr.get("region", ""))};'
            f'{vcard_escape(addr.get("postal", ""))};'
            f'{vcard_escape(addr.get("country", ""))}'
        )
        # Only emit if at least one component is non-empty
        if adr_value.replace(';', '').strip():
            if label and label not in ('HOME', 'WORK', 'OTHER'):
                prefix = f'item{item_counter}'
                item_counter += 1
                lines.append(f'{prefix}.ADR:{adr_value}')
                lines.append(f'{prefix}.X-ABLabel:{vcard_escape(addr.get("label", ""))}')
            else:
                lines.append(f'ADR{type_param}:{adr_value}')
    return lines



def _fold_vcard_line(line: str, limit: int = 75) -> str:
    """Fold a long vCard logical line per RFC 6350 §3.2.

    Encodes the line as UTF-8 and splits it into physical lines of at most
    `limit` octets each. Continuation lines are prefixed with a single space
    (which counts against the limit-1 octet budget for those lines). Multi-byte
    UTF-8 characters are never split: when a cut index falls on a continuation
    byte (0b10xxxxxx), the cut is backed up to the previous character boundary.

    Args:
        line: A single logical vCard property line (no CRLF).
        limit: Maximum octets per physical line (default 75, per RFC 6350).

    Returns:
        The folded string with CRLF between physical lines and no trailing CRLF.
    """
    encoded = line.encode('utf-8')
    if len(encoded) <= limit:
        return line

    def _safe_cut(data: bytes, max_octets: int) -> int:
        """Return the largest index ≤ max_octets that doesn't split a UTF-8 char."""
        idx = min(max_octets, len(data))  # clamp: idx must be a valid slice end
        # Back off past any UTF-8 continuation bytes (0b10xxxxxx)
        while idx > 0 and idx < len(data) and (data[idx] & 0b11000000) == 0b10000000:
            idx -= 1
        return idx

    chunks: list[str] = []
    # First chunk: up to `limit` octets
    cut = _safe_cut(encoded, limit)
    chunks.append(encoded[:cut].decode('utf-8'))
    remaining = encoded[cut:]

    # Continuation chunks: each prefixed with one space, so payload ≤ limit-1
    continuation_limit = limit - 1
    while remaining:
        cut = _safe_cut(remaining, min(continuation_limit, len(remaining)))
        if cut == 0:
            cut = 1  # safeguard: always advance at least one byte
        chunks.append(' ' + remaining[:cut].decode('utf-8'))
        remaining = remaining[cut:]

    return '\r\n'.join(chunks)


def _is_company_only(card: dict) -> bool:
    """Return True if a card represents a company (organisation, no person name).

    Such cards receive X-ABShowAs:COMPANY so they file and display under the
    company name on iPhone and Mac rather than under a blank person name.

    Args:
        card: Merged contact dict.

    Returns:
        True when the card has an organisation but no first or last name.
    """
    has_name = bool(card.get('first', '').strip() or card.get('last', '').strip())
    return bool(card.get('org', '').strip()) and not has_name


def render_vcard(card: dict) -> str:
    """Render one merged contact dict to a vCard 3.0 block.

    Produces a clean Apple-native import: name, nickname, ORG, TITLE, phones
    (with label fidelity), emails, addresses, BDAY, NOTE, and embedded iCloud
    PHOTO are kept. Google-specific data (websites, social profiles, group
    labels, custom fields, photo URLs, relations) is dropped.

    For iCloud-sourced contacts that were NOT merged with Google data, the
    original raw vCard block is returned verbatim with the clean field policy
    applied via _filter_raw_vcard().

    For Google-sourced and merged contacts, a full vCard 3.0 block is
    rendered from the structured contact dict.

    Args:
        card: Merged contact dict from merge_contacts().

    Returns:
        Complete VCARD block string with CRLF line endings.
    """
    # iCloud passthrough: return the original block verbatim (with apple-clean filter)
    if card.get('_source') == 'icloud' and card.get('_raw_vcard'):
        return _filter_raw_vcard(card['_raw_vcard'])

    # --- Build from structured dict (Google or merged) ---
    lines: list[str] = ['BEGIN:VCARD', 'VERSION:3.0']

    lines.append(f'FN:{vcard_escape(card["display"])}')

    # N: Last;First;Middle;Prefix;Suffix
    lines.append(
        f'N:{vcard_escape(card["last"])};'
        f'{vcard_escape(card["first"])};'
        f'{vcard_escape(card["middle"])};'
        f'{vcard_escape(card.get("prefix", ""))};'
        f'{vcard_escape(card.get("suffix", ""))}'
    )

    if card.get('nickname'):
        lines.append(f'NICKNAME:{vcard_escape(card["nickname"])}')

    # ORG: Company;Department
    org = card.get('org', '')
    dept = card.get('department', '')
    if org or dept:
        lines.append(f'ORG:{vcard_escape(org)};{vcard_escape(dept)}')

    if card.get('title'):
        lines.append(f'TITLE:{vcard_escape(card["title"])}')

    # TEL lines
    lines.extend(_render_phone_lines(card['phones']))

    # EMAIL lines (always included)
    lines.extend(_render_email_lines(card['emails']))

    # Addresses (native Apple field — kept)
    lines.extend(_render_address_lines(card.get('addresses', [])))

    # BDAY (native field — kept)
    if card.get('bday'):
        lines.append(f'BDAY:{vcard_escape(card["bday"])}')

    # NOTE (native field — kept)
    if card.get('notes'):
        lines.append(f'NOTE:{vcard_escape(card["notes"])}')

    # URLs, CATEGORIES, relations, custom fields, and photo URLs are
    # Google-specific / non-native Apple fields — dropped in the clean default.

    # For merged cards that originated from an iCloud passthrough, inject PHOTO
    # verbatim from the raw block — Apple-specific property with no Google dict
    # equivalent that would otherwise be silently dropped on re-render.
    # X-SOCIALPROFILE is always dropped (not a native iPhone field).
    if card.get('_raw_vcard'):
        lines.extend(_extract_passthrough_lines(card['_raw_vcard']))

    # Mark company-only contacts (organisation, no person name) so they file
    # and display as the company on iPhone and Mac.
    if _is_company_only(card):
        lines.append('X-ABShowAs:COMPANY')

    lines.append('END:VCARD')
    return '\r\n'.join(_fold_vcard_line(ln) for ln in lines) + '\r\n'


def _extract_passthrough_lines(raw: str) -> list[str]:
    """Extract PHOTO lines from a raw iCloud vCard block.

    Used when a Google+iCloud merged card is re-rendered from the dict —
    the embedded PHOTO property has no Google dict equivalent and would
    otherwise be silently dropped on re-render.

    X-SOCIALPROFILE is always dropped (not a native iPhone field).

    Args:
        raw: Raw iCloud vCard block (CRLF or LF line endings).

    Returns:
        List of verbatim vCard property lines (no CRLF) to append.
    """
    result: list[str] = []
    lines = [ln.rstrip('\r\n') for ln in raw.replace('\r\n', '\n').split('\n')]

    for line in lines:
        if not line or line.upper() in ('BEGIN:VCARD', 'END:VCARD'):
            continue
        colon = line.find(':')
        if colon < 0:
            continue
        seg0 = line[:colon].split(';')[0]
        prop_upper = seg0.upper() if '.' not in seg0 else seg0.partition('.')[2].upper()

        if prop_upper == 'PHOTO':
            result.append(line)

    return result


def _filter_raw_vcard(raw: str) -> str:
    """Apply the apple-clean field policy to a raw iCloud vCard block.

    Keeps everything that maps to a native Apple Contacts field: BEGIN, VERSION,
    FN, N, NICKNAME, ORG, TITLE, TEL, EMAIL, ADR, BDAY, NOTE, PHOTO,
    X-ABSHOWAS, X-ABADR. Drops everything else (URL, X-SOCIALPROFILE,
    CATEGORIES, X-ABRELATEDNAMES, X-GOOGLE-*, etc.).

    This preserves the original structure (item-groups, Apple extensions,
    encoding params) while removing non-native properties.

    Args:
        raw: Original vCard block with CRLF line endings.

    Returns:
        Filtered vCard block with CRLF line endings.
    """
    if not raw:
        return ''

    # Properties that map to native Apple Contacts fields.
    apple_clean_keep_props = {'BEGIN', 'VERSION', 'FN', 'N', 'NICKNAME', 'ORG',
                              'TITLE', 'TEL', 'EMAIL', 'ADR', 'BDAY', 'NOTE',
                              'PHOTO', 'X-ABSHOWAS', 'X-ABADR', 'END'}

    lines = [ln.rstrip('\r\n') for ln in raw.replace('\r\n', '\n').split('\n')]

    # First pass: collect item-group prefixes attached to kept properties.
    apple_keep_items: set[str] = set()
    for line in lines:
        colon = line.find(':')
        if colon < 0:
            continue
        seg0 = line[:colon].split(';')[0]
        if '.' in seg0:
            grp, _, prop = seg0.partition('.')
            if prop.upper() in apple_clean_keep_props:
                apple_keep_items.add(grp.lower())

    out: list[str] = []
    for line in lines:
        colon = line.find(':')
        if colon < 0:
            # Keep genuine fold continuations (RFC 6350 §3.2: start with a
            # space or tab); drop any other colon-less (malformed) line.
            if line[:1] in (' ', '\t'):
                out.append(line)
            continue

        seg0 = line[:colon].split(';')[0]

        if '.' in seg0:
            grp, _, base_prop = seg0.partition('.')
            grp_lower = grp.lower()
            prop_upper = base_prop.upper()
            if grp_lower not in apple_keep_items:
                continue
            if prop_upper not in apple_clean_keep_props and prop_upper != 'X-ABLABEL':
                continue
        else:
            prop_upper = seg0.upper()
            if prop_upper not in apple_clean_keep_props:
                continue

        out.append(line)

    return '\r\n'.join(out) + '\r\n'


# ---------------------------------------------------------------------------
# Field-manifest report
# ---------------------------------------------------------------------------

class FieldManifest:
    """Accumulates statistics about which field types were seen and kept/dropped.

    All counts are aggregate — no PII.
    """

    def __init__(self) -> None:
        # Per-field counts: (seen, kept)
        self.phones_seen = 0
        self.phones_kept = 0
        self.emails_seen = 0
        self.emails_kept = 0
        self.addresses_seen = 0
        self.addresses_kept = 0
        self.urls_seen = 0
        self.urls_kept = 0
        self.urls_social_seen = 0
        self.relations_seen = 0
        self.custom_fields_seen = 0
        self.bdays_seen = 0
        self.notes_seen = 0
        self.photo_urls_seen = 0
        self.photos_vcf_seen = 0     # PHOTO in vcf passthroughs
        self.social_profiles_seen = 0  # X-SOCIALPROFILE in vcf
        self.nicknames_seen = 0
        self.titles_seen = 0
        self.categories_seen = 0
        self.extra_vcards_seen = 0

    def tally(self, cards: list[dict]) -> None:
        """Tally field counts across all merged cards.

        Args:
            cards: List of merged contact dicts from merge_contacts().
        """
        for card in cards:
            self.phones_seen += len(card.get('phones', []))
            self.phones_kept += len(card.get('phones', []))

            # Emails are always kept in the clean default.
            self.emails_seen += len(card.get('emails', []))
            self.emails_kept += len(card.get('emails', []))

            # Addresses are always kept in the clean default.
            self.addresses_seen += len(card.get('addresses', []))
            self.addresses_kept += len(card.get('addresses', []))

            # URLs are always dropped (not a native iPhone field).
            for label, url in card.get('urls', []):
                self.urls_seen += 1
                if _is_social_url(url) or _is_social_label(label):
                    self.urls_social_seen += 1
            # urls_kept stays 0

            self.relations_seen += len(card.get('relations', []))
            self.custom_fields_seen += len(card.get('custom_fields', []))

            if card.get('bday'):
                self.bdays_seen += 1
            if card.get('notes'):
                self.notes_seen += 1
            if card.get('photo_url'):
                self.photo_urls_seen += 1
            if card.get('nickname'):
                self.nicknames_seen += 1
            if card.get('title'):
                self.titles_seen += 1
            if card.get('labels'):
                self.categories_seen += 1
            self.extra_vcards_seen += len(card.get('extra_vcards', []))

            # For iCloud passthrough blocks, scan the raw vcard for PHOTO and
            # X-SOCIALPROFILE counts (PHOTO kept, X-SOCIALPROFILE dropped).
            raw = card.get('_raw_vcard', '')
            if raw:
                for line in raw.split('\n'):
                    prop_upper = line.split(';')[0].split(':')[0].split('.')[-1].upper()
                    if prop_upper == 'PHOTO':
                        self.photos_vcf_seen += 1
                    elif prop_upper == 'X-SOCIALPROFILE':
                        self.social_profiles_seen += 1

    def print_report(self) -> None:
        """Print the field-manifest report (aggregate counts only, no PII)."""
        def _kept_str(kept: int, seen: int, reason: str = '') -> str:
            if seen == 0:
                return f'{seen:>4} seen'
            if kept == seen:
                return f'{seen:>4} seen, all kept'
            dropped = seen - kept
            return f'{seen:>4} seen, {kept} kept ({dropped} dropped{": " + reason if reason else ""})'

        print()
        print('  Field Manifest (aggregate counts, no PII)')
        print('  ' + '-' * 48)

        print(f'  TEL (phones)        : {_kept_str(self.phones_kept, self.phones_seen)}')
        print(f'  EMAIL               : {_kept_str(self.emails_kept, self.emails_seen)}')
        print(f'  ADR (addresses)     : {_kept_str(self.addresses_kept, self.addresses_seen)}')

        url_reason = 'all — not a native iPhone field' if self.urls_seen else ''
        print(f'  URL (websites)      : {_kept_str(self.urls_kept, self.urls_seen, url_reason)}')

        # Fields kept in the clean default (native Apple Contacts fields):
        print(f'  BDAY                : {_kept_str(self.bdays_seen, self.bdays_seen)}')
        print(f'  NOTE                : {_kept_str(self.notes_seen, self.notes_seen)}')

        # Fields dropped in the clean default (Google-specific / non-native):
        not_native = 'not a native iPhone field'
        print(f'  CATEGORIES          : {_kept_str(0, self.categories_seen, not_native)}')
        print(f'  NICKNAME            : {_kept_str(self.nicknames_seen, self.nicknames_seen)}')
        print(f'  TITLE               : {_kept_str(self.titles_seen, self.titles_seen)}')
        print(f'  Relations           : {_kept_str(0, self.relations_seen, not_native)}')
        print(f'  Custom fields       : {_kept_str(0, self.custom_fields_seen, not_native)}')
        print(f'  Photo (Google URL)  : {_kept_str(0, self.photo_urls_seen, "dead Google URL, not an image")}')
        print(f'  PHOTO (vCard)       : {_kept_str(self.photos_vcf_seen, self.photos_vcf_seen, "passthrough")}')
        print(f'  X-SOCIALPROFILE     : {_kept_str(0, self.social_profiles_seen, not_native)}')
        print(f'  Extra vCard lines   : {_kept_str(0, self.extra_vcards_seen, not_native)}')
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: parse arguments, read inputs, merge, write output."""
    parser = argparse.ArgumentParser(
        description=(
            'Merge Google Contacts CSV exports (and optional vCard exports, '
            "e.g. an iCloud export) into one clean, de-duplicated vCard ready "
            'to import into Apple Contacts / iCloud. Only contacts with a '
            'phone number are kept; duplicates that share a phone number are '
            'merged. Every field that has a native Apple Contacts equivalent '
            'is mapped and kept; Google-specific data is dropped (see the '
            "README's \"What's kept vs dropped\" table). To keep or drop "
            'different fields, fork the script and adapt render_vcard().'
        ),
    )
    parser.add_argument(
        '--version', action='version',
        version=f'google-contacts-to-icloud {__version__}',
    )
    parser.add_argument(
        '--input', default='./input',
        help='Directory containing CSV/vCard input files (default: ./input)',
    )
    parser.add_argument(
        '--output', default='./output/icloud-ready.vcf',
        help='Output vCard file path (default: ./output/icloud-ready.vcf)',
    )
    parser.add_argument(
        '--merge-by-name', action='store_true', default=False,
        help='Also merge contacts with identical full names (higher false-positive risk)',
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_path = Path(args.output)

    if not input_dir.is_dir():
        sys.exit(f'Error: input directory not found: {input_dir}')

    csv_files = sorted(input_dir.glob('*.csv'))
    vcf_files = sorted(input_dir.glob('*.vcf'))

    if not csv_files and not vcf_files:
        sys.exit(f'Error: no *.csv or *.vcf files found in {input_dir}')

    # --- Parse all inputs ---
    all_contacts: list[dict] = []
    total_input_rows = 0
    phone_bearing_rows = 0
    email_only_dropped = 0

    for csv_path in csv_files:
        contacts, kept, dropped = read_csv_file(csv_path)
        all_contacts.extend(contacts)
        total_input_rows += kept + dropped
        phone_bearing_rows += kept
        email_only_dropped += dropped

    vcf_contacts_total = 0
    for vcf_path in vcf_files:
        contacts, kept, dropped = parse_vcf_file(vcf_path)
        all_contacts.extend(contacts)
        vcf_contacts_total += kept + dropped
        phone_bearing_rows += kept
        email_only_dropped += dropped

    # --- Dedup by phone ---
    merged = merge_contacts(all_contacts)
    duplicates_merged = sum(c['_merged_count'] for c in merged)

    # --- Optional: dedup by name ---
    if args.merge_by_name:
        merged = merge_by_name_pass(merged)

    # --- Prefer the '+1' (E.164) form when a number appears in both formats ---
    for card in merged:
        card['phones'] = _dedupe_phones_prefer_plus(card['phones'])

    # --- Count distinct normalized phones ---
    all_norms: set[str] = set()
    for card in merged:
        all_norms.update(card['phone_norms'])

    # --- Field manifest ---
    manifest = FieldManifest()
    manifest.tally(merged)

    # --- Write output ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as fh:
        for card in merged:
            fh.write(render_vcard(card))

    # --- Aggregate report (no PII) ---
    print('=' * 52)
    print('  google-contacts-to-icloud — Merge Report')
    print('=' * 52)
    print(f'  CSV files processed        : {len(csv_files)}')
    print(f'  vCard files processed      : {len(vcf_files)}')
    print(f'  Total input contacts       : {total_input_rows + vcf_contacts_total:,}')
    print(f'  Phone-bearing kept         : {phone_bearing_rows:,}')
    print(f'  Phone-less dropped         : {email_only_dropped:,}')
    print(f'  Duplicate cards merged     : {duplicates_merged:,}')
    print(f'  Final unique contacts      : {len(merged):,}')
    print(f'  Distinct phone numbers     : {len(all_norms):,}')
    print(f'  Output written to          : {output_path}')
    print('=' * 52)
    manifest.print_report()


if __name__ == '__main__':
    main()

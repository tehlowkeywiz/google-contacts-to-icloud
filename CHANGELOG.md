# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-06-24

### Added

- Initial public release.
- Single-file Python CLI (`merge_contacts.py`) with zero third-party dependencies.
- Phone-only filter: contacts without a phone number are dropped, eliminating Google's "Other contacts" bulk.
- Deduplication by normalized phone number across multiple Google CSV exports and an optional iCloud vCard export.
- Apple-native field mapping: names, nickname, company, job title, all phone labels, emails, addresses, birthday, notes, and embedded iCloud photos are kept; Google-specific data (websites, social profiles, group labels, custom fields, photo URLs, relations) is dropped.
- Phone-label fidelity: standard labels map to `TEL;TYPE=…`; custom/non-standard labels (iPhone, Telegram, etc.) rendered as Apple item-group pairs (`itemN.TEL` + `itemN.X-ABLabel`).
- E.164 / `+1` preference: when the same number appears with and without a country-code prefix, the prefixed form is kept.
- Company-only contacts (organization with no person name) flagged `X-ABShowAs:COMPANY`.
- iCloud vCard passthrough: contacts not merged with a Google record are emitted from their original vCard block with the field policy applied (preserving embedded photos and item-groups).
- Aggregate field-manifest report printed to stdout after every run (no PII).
- 120 unit and integration tests covering the full pipeline against anonymized fixtures.
- CI on Python 3.10–3.13 via GitHub Actions.
- pipx-installable via `pipx install git+https://github.com/tehlowkeywiz/google-contacts-to-icloud.git`.

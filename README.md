# google-contacts-to-icloud

Merge all your Google Contacts exports into one clean, de-duplicated vCard for Apple Contacts / iCloud — zero dependencies, runs locally, your data never leaves your machine.

[![tests](https://github.com/tehlowkeywiz/google-contacts-to-icloud/actions/workflows/test.yml/badge.svg)](https://github.com/tehlowkeywiz/google-contacts-to-icloud/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org)
[![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)

A single-file Python CLI that takes your Google Contacts CSV exports (and,
optionally, an iCloud contacts export), keeps only the contacts that have a
phone number, maps every field that has a native iPhone / Apple Contacts
equivalent, and produces one clean, de-duplicated `.vcf` ready to import.

**The problem it solves:** If you've used Gmail for years, Google accumulates
thousands of "Other contacts" — people who emailed you once, auto-saved with
no phone number. This tool throws all of that away and gives you a clean phone
book: contacts with actual phone numbers, mapped to native iPhone fields,
merged and deduplicated across multiple Google accounts (and your existing
iCloud contacts, if you include them).

It is **opinionated by design**: run it with no flags and you get a clean
import. There's nothing to configure for the common case.

---

> ⚠️ **Data-loss warning — read before you start**
>
> The clean cutover this tool enables (one merged file in iCloud, no Google
> clutter) requires **deleting all contacts from your iCloud account** before
> importing. That deletion syncs to every signed-in iPhone, iPad, and Mac
> immediately, and there is no built-in undo.
>
> **Before you delete anything:**
>
> 1. **Back up first — not optional.** In Contacts.app: **File → Export →
>    Contacts Archive (.abbu)**. This is a full snapshot. To restore: double-click
>    the `.abbu` file and Contacts.app replaces everything with the backed-up set.
>    Keep it somewhere safe until you've confirmed the merged list is correct.
> 2. **Verify the output before deleting.** Open `output/icloud-ready.vcf` (or
>    check the count the tool printed) and confirm it looks right — expected
>    names present, count is plausible — **before** touching your live iCloud
>    contacts.
> 3. **Keep your source exports until you're satisfied.** Don't remove your
>    Google contacts or the CSV/vCard files until the merged list has been live
>    for a few days and you've confirmed nothing is missing. They're your
>    secondary backup.
>
> **No warranty.** This tool is provided as-is under the MIT licence; you are
> responsible for your own data. To preview the output format safely, run it
> against the anonymized fixtures first:
> `python3 merge_contacts.py --input tests/fixtures --output /tmp/preview.vcf`.

---

## What's kept vs dropped (Google → iPhone)

By **default** (no flags), the tool keeps every field that maps to a native
iPhone / Apple Contacts field and drops Google-specific data that has no clean
Apple equivalent (or that's just noise).

**✅ Kept — mapped to native iPhone Contacts fields:**

| Google field | → iPhone / Apple Contacts field |
|---|---|
| First / Middle / Last name, Prefix, Suffix | Name |
| Nickname | Nickname |
| Organization Name | Company |
| Organization Department | Company (department) |
| Organization Title | Job Title |
| Phone numbers — **all labels preserved** (mobile, iPhone, work, home, fax, pager, custom…) | Phone |
| Email addresses (with labels) | Email |
| Addresses | Address |
| Birthday | Birthday |
| Notes | Notes |
| *(from an iCloud `.vcf`)* embedded contact photo | Photo |

Plus: a contact that has a **Company but no person name** is automatically
flagged `X-ABShowAs:COMPANY`, so it files and displays under the company name
(e.g. a pharmacy or store) — the same as ticking the "Company" box on macOS.

**🗑️ Dropped — Google-specific, or no native iPhone target:**

| Google field | Why it's dropped |
|---|---|
| Websites / URLs | Not part of a clean phone book |
| Social profiles (Facebook, LinkedIn, Twitter, AngelList…) | Not native contact data |
| Labels / group membership | Google groups don't import into iCloud anyway |
| Custom fields | Undefined Google-specific data |
| Photo (Google URL) | A dead Google-server link, not an actual image |
| Phonetic name, File As | Rarely used; no clean Apple target |
| Relations (spouse, assistant…) | Apple item-group field; dropped for a lean book |

**Want a different set of fields?** This tool is intentionally opinionated. To
keep or drop something different, **fork the repo and adapt `render_vcard()`**
— all the field logic lives in that one function.

---

## Get started in 30 seconds

> ⚠️ **Back up your contacts before importing** — see the data-loss warning at the top of this page.

```bash
# Install (via pipx) and run:
pipx install git+https://github.com/tehlowkeywiz/google-contacts-to-icloud.git
mkdir input   # drop your Google CSV exports (+ optional iCloud .vcf) here
google-contacts-to-icloud
# then import output/icloud-ready.vcf into iCloud
```

No pip / no dependencies? Clone and run the single file instead — see [Quick start](#quick-start) below for both options and [Step-by-step usage](#step-by-step-usage) for the full walkthrough.

---

## Example output

Two cards from the bundled sample data show the shape of the output:

```
BEGIN:VCARD
VERSION:3.0
FN:Jeanne Tremblay
N:Tremblay;Jeanne;;;
ORG:Jean-Coutu;
TITLE:Pharmacienne
TEL;TYPE=CELL:+1 (514) 555-0142
item1.TEL;type=CELL:+1 (514) 555-0199
item1.X-ABLabel:iPhone
TEL;TYPE=WORK:+1 (514) 555-0100
EMAIL;TYPE=WORK:jeanne@example.com
ADR;TYPE=WORK:;;123 Rue Principale;Montreal;QC;H2X 1Y4;Canada
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Pharmacie Centrale
N:;;;;
ORG:Pharmacie Centrale;
TEL;TYPE=MAIN:+1 (450) 555-0177
X-ABShowAs:COMPANY
END:VCARD
```

The first is a person — name, company, **job title**, three phones with their
**labels preserved** (mobile, iPhone, work), email, and address. The source
contact's LinkedIn URL and Google group label were dropped. The second is a
company-only contact (no person name), flagged `X-ABShowAs:COMPANY` so it files
and displays under the company name.

Running the tool against the bundled fixtures (`python3 merge_contacts.py --input tests/fixtures --output /tmp/preview.vcf`) produces a report like this — aggregate counts only, no personal data:

```
====================================================
  google-contacts-to-icloud — Merge Report
====================================================
  CSV files processed        : 3
  vCard files processed      : 1
  Total input contacts       : 12
  Phone-bearing kept         : 9
  Phone-less dropped         : 3
  Duplicate cards merged     : 2
  Final unique contacts      : 7
  Distinct phone numbers     : 10
  Output written to          : /tmp/pp-preview.vcf
====================================================

  Field Manifest (aggregate counts, no PII)
  ------------------------------------------------
  TEL (phones)        :   10 seen, all kept
  EMAIL               :    5 seen, all kept
  ADR (addresses)     :    1 seen, all kept
  URL (websites)      :    2 seen, 0 kept (2 dropped: all — not a native iPhone field)
  BDAY                :    1 seen, all kept
  NOTE                :    1 seen, all kept
  CATEGORIES          :    0 seen
  NICKNAME            :    1 seen, all kept
  TITLE               :    1 seen, all kept
  Relations           :    1 seen, 0 kept (1 dropped: not a native iPhone field)
  Custom fields       :    1 seen, 0 kept (1 dropped: not a native iPhone field)
  Photo (Google URL)  :    0 seen
  PHOTO (vCard)       :    1 seen, all kept
  X-SOCIALPROFILE     :    1 seen, 0 kept (1 dropped: not a native iPhone field)
  Extra vCard lines   :    0 seen
```

---

## PRIVACY

Your CSV exports and vCard files contain real names, phone numbers, and email
addresses. This tool runs entirely on your machine — nothing is uploaded anywhere.

**Important:** The `.gitignore` in this repo is set up to prevent you from
accidentally committing real data. The `input/` folder, `output/` folder,
all `.vcf` files, and any `contacts-*.csv` files are gitignored. The only
CSV and vCard files that can be committed are the anonymized test fixtures
in `tests/fixtures/`. If you fork this repo, do not override those ignore rules.

---

## Step-by-step usage

### 1. Export your Google Contacts

For each Google account you want to merge:

1. Go to [contacts.google.com](https://contacts.google.com)
2. Click **Export** (left sidebar or gear icon)
3. Choose **All contacts** and format **Google CSV**
4. Download the file
5. Repeat for **Other contacts** (same export flow, different group)

You'll end up with one or more `.csv` files per account.

### 2. (Optional) Export your existing iCloud contacts

If you already have contacts in iCloud you want to preserve through the
cutover, export them first so they get merged in:

1. Open **Contacts.app** on your Mac
2. Select all contacts: **Edit → Select All** (or ⌘A)
3. **File → Export → Export vCard...**
4. Save the `.vcf` file

Drop that file into `./input/` along with your Google CSVs. The tool parses it,
applies the same phone-only filter, and merges any iCloud contact that shares a
phone number with a Google contact into a single card.

> **Naming note:** the output file is `output/icloud-ready.vcf`. Give your
> iCloud export any other name (e.g. `icloud-export.vcf`) to avoid confusing it
> with the output.

### 3. Drop all files in `./input/`

```bash
mkdir input
cp ~/Downloads/contacts-*.csv input/
cp ~/Downloads/icloud-export.vcf input/   # optional; any name except icloud-ready.vcf
```

All `*.csv` and `*.vcf` files in `input/` are processed. File names don't matter.

### 4. Run the tool

```bash
python3 merge_contacts.py
```

That's it — no flags needed. The tool prints an aggregate report (no personal
data) and writes the output to `./output/icloud-ready.vcf`.

### 5. Import to iCloud

> ⚠️ **Back up before this step** (Contacts.app → File → Export → Contacts
> Archive) and verify the output count looks right. The delete-then-import
> sequence below syncs to all your devices immediately and cannot be undone
> without a backup.

To get a clean result, delete your existing iCloud contacts first, then import
the merged file:

1. Go to [icloud.com](https://icloud.com) → Contacts
2. Select all: click any contact, then **Edit → Select All**
3. Delete selected (gear icon → Delete) — clears iCloud contacts on all devices
4. Click the gear icon → **Import vCard...**
5. Select `output/icloud-ready.vcf`

**Import-only (no delete step):** in Contacts.app with iCloud as the default
account, **File → Import** the `.vcf`. This merges into your existing contacts
rather than replacing them — duplicates may result.

### 6. Clean up and switch your iPhone

After verifying your iCloud contacts look right:

1. On your iPhone: **Settings → Apps → Contacts → Default Account → iCloud**
2. Optionally turn off **Google Contacts sync** (Settings → Mail → Accounts →
   your Google account → toggle off Contacts) so the old Google list doesn't
   reappear.

---

## How it works

1. **Reads** every `*.csv` (Google export) and `*.vcf` (e.g. iCloud export) in
   the input directory. CSVs are parsed as Google's schema (UTF-8 with BOM);
   vCards as 3.0 or 4.0.
2. **Filters** — keeps only contacts with at least one real phone number; the
   email-only "Other contacts" bulk is discarded.
3. **Normalizes** phone numbers to digits-only (and strips the leading NANP `1`)
   for comparison.
4. **Merges** contacts that share a normalized phone number into one card —
   across sources, so a Google contact and an iCloud contact with the same
   number become a single card (union of phones/emails, most complete name).
5. **Maps** each contact to native Apple Contacts fields (see the kept/dropped
   table above) and **writes** a clean vCard 3.0 `.vcf`, then prints a
   field-manifest report (aggregate counts, no personal data).

### Phone label fidelity

Standard labels map to `TEL;TYPE=<TYPE>`:

| Input label | Output TYPE |
|---|---|
| Mobile / Cell / (unlabeled) | `CELL` |
| Home | `HOME` |
| Work / Office | `WORK` |
| Main | `MAIN` |
| Pager | `PAGER` |
| Other | `OTHER` |
| Fax | `FAX` |
| Home Fax | `HOME,FAX` |
| Work Fax | `WORK,FAX` |

Non-standard / custom labels (**iPhone**, Service, Telegram, etc.) are rendered
as an item-group pair so the exact label is preserved in Apple Contacts:

```
item1.TEL;type=CELL:+15551234567
item1.X-ABLabel:iPhone
```

---

## Options

The default run needs **no flags** — it produces the clean import described
above. Only three options exist:

| Flag | Default | Effect |
|---|---|---|
| `--input DIR` | `./input` | Directory containing the CSV/vCard input files |
| `--output FILE` | `./output/icloud-ready.vcf` | Output vCard file path |
| `--merge-by-name` | off | Also merge cards with identical full names. Higher false-positive risk — two different people named "John Smith" would be collapsed. |

To change which fields are kept or dropped, **fork the repo and edit
`render_vcard()`** — the field logic is all in one place.

---

## Quick start

Two fast paths — pick one. For the full walkthrough (exporting from Google, iCloud cutover, iPhone switch), see [Step-by-step usage](#step-by-step-usage).

**Option A — install as a command** (via [pipx](https://pipx.pypa.io)):

```bash
pipx install git+https://github.com/tehlowkeywiz/google-contacts-to-icloud.git
mkdir input   # drop your Google CSV exports + optional iCloud .vcf here
google-contacts-to-icloud
# back up first, then import output/icloud-ready.vcf into iCloud
```

**Option B — clone and run the single file** (no install needed):

```bash
git clone https://github.com/tehlowkeywiz/google-contacts-to-icloud.git
cd google-contacts-to-icloud
mkdir input
# drop your Google CSV exports and optional iCloud .vcf into input/
python3 merge_contacts.py
# back up first, then import output/icloud-ready.vcf into iCloud
```

## Requirements

- Python 3.10 or later (uses `X | None` type-union syntax)
- No third-party dependencies — uses only the standard library

---

## Running the tests

The test suite uses the anonymized fixtures in `tests/fixtures/`. No real
contact data is required. The suite is **120 tests and runs in under two
seconds** — integration tests drive the full pipeline end-to-end against the
anonymized fixtures.

```bash
# With pytest installed:
python3 -m pytest tests/ -v

# Or with stdlib unittest (no pip needed):
python3 tests/test_merge.py
```

---

## Limitations

- **Google CSV + vCard formats only.** Reads Google's CSV schema and standard
  vCard 3.0 / 4.0. Does not support Outlook CSV, Yahoo CSV, or other formats.
- **Phone dedup is exact after normalization.** The same person with the same
  number in two sources (with vs. without country code) merges correctly, but a
  typo'd number won't.
- **Phone dedup is a single greedy pass.** If a "bridge" contact shares one
  number with A and a *different* number with B (and A and B share none), the
  three won't all collapse into one card — you'll get a near-duplicate. Apple
  Contacts' **Card → Look for Duplicates** cleans these up after import.
- **Name dedup is off by default.** `--merge-by-name` exists but risks
  collapsing unrelated people who share a name.
- **Photos come only from iCloud.** Google's photo field is a dead URL, not an
  image, so it's dropped. Embedded photos from an iCloud `.vcf` are carried
  through verbatim.
- **Multi-value cells** using Google's ` ::: ` separator are handled.

---

## Project structure

```
merge_contacts.py        — the tool (single file, no dependencies)
README.md                — this file
CLAUDE.md                — guide for AI agents working on the repo
LICENSE                  — MIT
pyproject.toml           — packaging metadata (pipx-installable)
.gitignore               — prevents committing real contact data
.github/workflows/
  test.yml               — CI: tests on Python 3.10–3.13 + ruff lint
tests/
  test_merge.py          — unit + integration tests (120 tests)
  fixtures/              — anonymized fixtures (fake 555-numbers, placeholder names)
    account-a.csv        — fake Google CSV: phone contacts + email-only contact
    account-b.csv        — fake Google CSV: duplicate Jane + unique Carlos
    account-other.csv    — fake Google CSV: multi-number contact + email-only
    icloud-export.vcf    — fake iCloud vCard: exercises vCard parsing + merge
input/                   — gitignored: put your real exports here
output/                  — gitignored: the generated .vcf lands here
```

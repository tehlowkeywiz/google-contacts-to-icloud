"""
Tests for merge_contacts.py using the anonymized fixtures in tests/fixtures/.

Run with:  python3 -m pytest tests/  OR  python3 tests/test_merge.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Allow importing merge_contacts from the repo root
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import merge_contacts as mc  # noqa: E402  # import after sys.path setup is intentional


FIXTURE_DIR = Path(__file__).parent / 'fixtures'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_report_line(stdout: str, label: str) -> int | None:
    """Extract the integer value from a report line matching `label`."""
    for line in stdout.splitlines():
        if label in line:
            return int(line.split(':')[1].strip().replace(',', ''))
    return None


def _write_vcf(content: str) -> Path:
    """Write a temporary .vcf file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        suffix='.vcf', mode='w', encoding='utf-8', delete=False
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Unit: phone normalization
# ---------------------------------------------------------------------------

class TestNormalizePhone(unittest.TestCase):
    def test_strips_formatting(self):
        self.assertEqual(mc.normalize_phone('+1 (555) 123-4567'), '5551234567')

    def test_strips_leading_1_for_11digit_nanp(self):
        self.assertEqual(mc.normalize_phone('15551234567'), '5551234567')

    def test_leaves_10_digit_unchanged(self):
        self.assertEqual(mc.normalize_phone('5551234567'), '5551234567')

    def test_international_not_stripped(self):
        # 12-digit number starting with 1 — not NANP, keep all digits
        self.assertEqual(mc.normalize_phone('123456789012'), '123456789012')


class TestDedupePhonesPreferPlus(unittest.TestCase):
    def test_plus_form_wins_when_seen_second(self):
        phones = [('5145551234', '514-555-1234', 'Mobile'),
                  ('5145551234', '+1 (514) 555-1234', 'Mobile')]
        result = mc._dedupe_phones_prefer_plus(phones)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], '+1 (514) 555-1234')

    def test_plus_form_wins_when_seen_first(self):
        phones = [('5145551234', '+1 (514) 555-1234', 'Mobile'),
                  ('5145551234', '514-555-1234', 'Mobile')]
        result = mc._dedupe_phones_prefer_plus(phones)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], '+1 (514) 555-1234')

    def test_distinct_numbers_all_kept_in_order(self):
        phones = [('5145551234', '514-555-1234', 'Mobile'),
                  ('4505550177', '+1 450-555-0177', 'Work')]
        result = mc._dedupe_phones_prefer_plus(phones)
        self.assertEqual([r[0] for r in result], ['5145551234', '4505550177'])

    def test_two_bare_forms_keep_first(self):
        phones = [('5145551234', '514.555.1234', 'Mobile'),
                  ('5145551234', '(514) 555-1234', 'Mobile')]
        result = mc._dedupe_phones_prefer_plus(phones)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], '514.555.1234')


# ---------------------------------------------------------------------------
# Unit: vCard escaping / unescaping round-trip
# ---------------------------------------------------------------------------

class TestVcardEscape(unittest.TestCase):
    def test_escapes_semicolons(self):
        self.assertIn('\\;', mc.vcard_escape('a;b'))

    def test_escapes_commas(self):
        self.assertIn('\\,', mc.vcard_escape('a,b'))

    def test_escapes_backslash(self):
        self.assertIn('\\\\', mc.vcard_escape('a\\b'))

    def test_newline_becomes_literal_n(self):
        self.assertIn('\\n', mc.vcard_escape('a\nb'))


class TestVcardUnescape(unittest.TestCase):
    def test_unescapes_semicolons(self):
        self.assertEqual(mc.vcard_unescape('a\\;b'), 'a;b')

    def test_unescapes_commas(self):
        self.assertEqual(mc.vcard_unescape('a\\,b'), 'a,b')

    def test_unescapes_backslash(self):
        self.assertEqual(mc.vcard_unescape('a\\\\b'), 'a\\b')

    def test_unescapes_newline(self):
        self.assertEqual(mc.vcard_unescape('a\\nb'), 'a\nb')

    def test_round_trip(self):
        original = 'O\'Brien; Smith, Inc. \\ Co.'
        self.assertEqual(mc.vcard_unescape(mc.vcard_escape(original)), original)


# ---------------------------------------------------------------------------
# Unit: phone label → vCard TYPE / item-group fidelity
# ---------------------------------------------------------------------------

class TestPhoneLabelToType(unittest.TestCase):
    def test_mobile_maps_to_cell(self):
        self.assertEqual(mc.phone_label_to_type('Mobile'), 'CELL')

    def test_work_maps_to_work(self):
        self.assertEqual(mc.phone_label_to_type('Work'), 'WORK')

    def test_home_maps_to_home(self):
        self.assertEqual(mc.phone_label_to_type('Home'), 'HOME')

    def test_main_maps_to_main(self):
        self.assertEqual(mc.phone_label_to_type('Main'), 'MAIN')

    def test_pager_maps_to_pager(self):
        self.assertEqual(mc.phone_label_to_type('Pager'), 'PAGER')

    def test_other_maps_to_other(self):
        self.assertEqual(mc.phone_label_to_type('Other'), 'OTHER')

    def test_fax_maps_to_fax(self):
        self.assertEqual(mc.phone_label_to_type('Fax'), 'FAX')

    def test_home_fax_maps_to_home_fax(self):
        self.assertEqual(mc.phone_label_to_type('Home Fax'), 'HOME,FAX')

    def test_work_fax_maps_to_work_fax(self):
        self.assertEqual(mc.phone_label_to_type('Work Fax'), 'WORK,FAX')

    def test_iphone_returns_empty_for_item_group(self):
        # iPhone is custom — should trigger item-group rendering
        self.assertEqual(mc.phone_label_to_type('iPhone'), '')

    def test_service_returns_empty_for_item_group(self):
        self.assertEqual(mc.phone_label_to_type('Service'), '')

    def test_telegram_returns_empty_for_item_group(self):
        self.assertEqual(mc.phone_label_to_type('Telegram'), '')

    def test_unlabeled_defaults_to_cell(self):
        self.assertEqual(mc.phone_label_to_type(''), 'CELL')

    def test_starred_label_stripped(self):
        # Google prefixes default labels with "* "
        self.assertEqual(mc.phone_label_to_type('* Mobile'), 'CELL')

    def test_vcard_cell_type(self):
        self.assertEqual(mc.phone_label_to_type('CELL'), 'CELL')

    def test_vcard_work_type(self):
        self.assertEqual(mc.phone_label_to_type('WORK'), 'WORK')


class TestRenderPhoneLines(unittest.TestCase):
    """Phone rendering: standard TYPE vs item-group for custom labels."""

    def test_work_label_renders_type(self):
        lines = mc._render_phone_lines([('5551234567', '+15551234567', 'Work')])
        self.assertEqual(len(lines), 1)
        self.assertIn('TYPE=WORK', lines[0])
        self.assertNotIn('X-ABLabel', lines[0])

    def test_iphone_label_renders_item_group(self):
        lines = mc._render_phone_lines([('5551234567', '+15551234567', 'iPhone')])
        self.assertEqual(len(lines), 2)
        # First line: itemN.TEL;type=CELL:…
        self.assertRegex(lines[0], r'^item\d+\.TEL')
        self.assertIn('type=CELL', lines[0])
        # Second line: itemN.X-ABLabel:iPhone
        self.assertRegex(lines[1], r'^item\d+\.X-ABLabel:iPhone')

    def test_custom_label_renders_item_group(self):
        lines = mc._render_phone_lines([('5551234567', '+15551234567', 'Service')])
        self.assertEqual(len(lines), 2)
        self.assertIn('X-ABLabel:Service', lines[1])

    def test_unlabeled_defaults_to_cell_type(self):
        lines = mc._render_phone_lines([('5551234567', '+15551234567', '')])
        self.assertEqual(len(lines), 1)
        self.assertIn('TYPE=CELL', lines[0])

    def test_multiple_phones_increment_item_numbers(self):
        phones = [
            ('5551111111', '+15551111111', 'iPhone'),
            ('5552222222', '+15552222222', 'Telegram'),
        ]
        lines = mc._render_phone_lines(phones)
        # Two custom labels → 4 lines total (2 pairs)
        self.assertEqual(len(lines), 4)
        # Item numbers should differ
        self.assertNotEqual(lines[0].split('.')[0], lines[2].split('.')[0])

    def test_home_fax_renders_multi_type(self):
        lines = mc._render_phone_lines([('5551234567', '+15551234567', 'Home Fax')])
        self.assertEqual(len(lines), 1)
        self.assertIn('TYPE=HOME,FAX', lines[0])


# ---------------------------------------------------------------------------
# Unit: vCard line unfolding
# ---------------------------------------------------------------------------

class TestUnfoldVcardLines(unittest.TestCase):
    def test_continuation_line_joined(self):
        text = 'TEL;TYPE=CELL:+1 (555) 123\n -4567'
        lines = mc._unfold_vcard_lines(text)
        self.assertEqual(lines, ['TEL;TYPE=CELL:+1 (555) 123-4567'])

    def test_tab_continuation_joined(self):
        text = 'FN:Jane\n\tDoe'
        lines = mc._unfold_vcard_lines(text)
        self.assertEqual(lines, ['FN:JaneDoe'])

    def test_no_folding_unchanged(self):
        text = 'BEGIN:VCARD\nFN:Alice\nEND:VCARD'
        lines = mc._unfold_vcard_lines(text)
        self.assertEqual(lines, ['BEGIN:VCARD', 'FN:Alice', 'END:VCARD'])


# ---------------------------------------------------------------------------
# Unit: vCard property line parsing
# ---------------------------------------------------------------------------

class TestParseVcardProperty(unittest.TestCase):
    def test_simple_property(self):
        name, params, value, group = mc._parse_vcard_property('FN:Jane Doe')
        self.assertEqual(name, 'FN')
        self.assertEqual(value, 'Jane Doe')
        self.assertEqual(params, {})
        self.assertEqual(group, '')

    def test_property_with_type_param(self):
        name, params, value, group = mc._parse_vcard_property('TEL;TYPE=CELL:+15551234567')
        self.assertEqual(name, 'TEL')
        self.assertEqual(params.get('TYPE'), ['CELL'])
        self.assertEqual(value, '+15551234567')

    def test_property_with_bare_param(self):
        name, params, value, group = mc._parse_vcard_property('TEL;CELL:+15551234567')
        self.assertEqual(name, 'TEL')
        self.assertIn('CELL', params.get('TYPE', []))

    def test_property_multi_type(self):
        name, params, value, group = mc._parse_vcard_property('TEL;TYPE=WORK,VOICE:+15559990000')
        self.assertEqual(name, 'TEL')
        self.assertIn('WORK', params.get('TYPE', []))

    def test_item_group_prefix_returned(self):
        name, params, value, group = mc._parse_vcard_property(
            'item1.TEL;type=CELL;type=pref:+1 (555) 200-3000'
        )
        self.assertEqual(name, 'TEL')
        self.assertEqual(group, 'item1')
        self.assertIn('CELL', params.get('TYPE', []))
        self.assertEqual(value, '+1 (555) 200-3000')

    def test_x_ablabel_parsed(self):
        name, params, value, group = mc._parse_vcard_property(
            'item1.X-ABLabel:iPhone'
        )
        self.assertEqual(name, 'X-ABLABEL')
        self.assertEqual(group, 'item1')
        self.assertEqual(value, 'iPhone')


# ---------------------------------------------------------------------------
# Unit: Google CSV row parsing — lossless fields
# ---------------------------------------------------------------------------

class TestParseCsvRow(unittest.TestCase):
    def _base_row(self) -> dict:
        """Minimal Google CSV row with all expected columns present."""
        return {
            'First Name': '', 'Middle Name': '', 'Last Name': '',
            'Name Prefix': '', 'Name Suffix': '', 'Nickname': '',
            'Organization Name': '', 'Organization Title': '',
            'Organization Department': '',
            'Birthday': '', 'Notes': '', 'Photo': '', 'Labels': '',
            'E-mail 1 - Label': '', 'E-mail 1 - Value': '',
            'E-mail 2 - Label': '', 'E-mail 2 - Value': '',
            'Phone 1 - Label': '', 'Phone 1 - Value': '',
            'Phone 2 - Label': '', 'Phone 2 - Value': '',
            'Address 1 - Label': '', 'Address 1 - Formatted': '',
            'Address 1 - Street': '', 'Address 1 - City': '',
            'Address 1 - PO Box': '', 'Address 1 - Region': '',
            'Address 1 - Postal Code': '', 'Address 1 - Country': '',
            'Address 1 - Extended Address': '',
            'Website 1 - Label': '', 'Website 1 - Value': '',
            'Relation 1 - Label': '', 'Relation 1 - Value': '',
            'Custom Field 1 - Label': '', 'Custom Field 1 - Value': '',
        }

    def test_email_only_returns_none(self):
        row = self._base_row()
        row['E-mail 1 - Value'] = 'test@example.com'
        self.assertIsNone(mc.parse_csv_row(row))

    def test_phone_bearing_returns_contact(self):
        row = self._base_row()
        row['First Name'] = 'Alice'
        row['Last Name'] = 'Wonder'
        row['Phone 1 - Label'] = 'Mobile'
        row['Phone 1 - Value'] = '+1 (555) 111-2222'
        result = mc.parse_csv_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(result['display'], 'Alice Wonder')
        self.assertEqual(len(result['phones']), 1)
        self.assertEqual(result['phones'][0][0], '5551112222')

    def test_phone_label_preserved_as_third_tuple_element(self):
        row = self._base_row()
        row['First Name'] = 'Alice'
        row['Phone 1 - Label'] = 'iPhone'
        row['Phone 1 - Value'] = '+1 (555) 111-2222'
        result = mc.parse_csv_row(row)
        self.assertIsNotNone(result)
        # Third element is the raw label string
        self.assertEqual(result['phones'][0][2], 'iPhone')

    def test_work_label_preserved(self):
        row = self._base_row()
        row['First Name'] = 'Bob'
        row['Phone 1 - Label'] = 'Work'
        row['Phone 1 - Value'] = '+15551234567'
        result = mc.parse_csv_row(row)
        self.assertEqual(result['phones'][0][2], 'Work')

    def test_multi_value_phones_split_on_separator(self):
        row = self._base_row()
        row['First Name'] = 'Multi'
        row['Phone 1 - Label'] = 'Mobile'
        row['Phone 1 - Value'] = '+1 (555) 777-8888 ::: +1 (555) 999-0000'
        result = mc.parse_csv_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['phones']), 2)

    def test_org_used_as_fallback_name(self):
        row = self._base_row()
        row['Organization Name'] = 'Acme Corp'
        row['Phone 1 - Value'] = '+1 (555) 300-4000'
        result = mc.parse_csv_row(row)
        self.assertEqual(result['display'], 'Acme Corp')

    def test_address_parsed_to_structured_dict(self):
        row = self._base_row()
        row['First Name'] = 'Addr'
        row['Phone 1 - Value'] = '+15550001111'
        row['Address 1 - Label'] = 'Home'
        row['Address 1 - Street'] = '123 Elm St'
        row['Address 1 - City'] = 'Springfield'
        row['Address 1 - Region'] = 'ON'
        row['Address 1 - Postal Code'] = 'K1A 0A1'
        row['Address 1 - Country'] = 'Canada'
        result = mc.parse_csv_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['addresses']), 1)
        addr = result['addresses'][0]
        self.assertEqual(addr['street'], '123 Elm St')
        self.assertEqual(addr['city'], 'Springfield')
        self.assertEqual(addr['country'], 'Canada')
        self.assertEqual(addr['label'], 'Home')

    def test_website_parsed(self):
        row = self._base_row()
        row['First Name'] = 'Web'
        row['Phone 1 - Value'] = '+15550002222'
        row['Website 1 - Label'] = 'Profile'
        row['Website 1 - Value'] = 'https://example.com/profile'
        result = mc.parse_csv_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['urls']), 1)
        self.assertEqual(result['urls'][0][0], 'Profile')
        self.assertEqual(result['urls'][0][1], 'https://example.com/profile')

    def test_custom_field_preserved(self):
        row = self._base_row()
        row['First Name'] = 'Custom'
        row['Phone 1 - Value'] = '+15550003333'
        row['Custom Field 1 - Label'] = 'Favourite Colour'
        row['Custom Field 1 - Value'] = 'Blue'
        result = mc.parse_csv_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(len(result['custom_fields']), 1)
        self.assertEqual(result['custom_fields'][0][0], 'Favourite Colour')
        self.assertEqual(result['custom_fields'][0][1], 'Blue')

    def test_birthday_preserved(self):
        row = self._base_row()
        row['First Name'] = 'Bday'
        row['Phone 1 - Value'] = '+15550004444'
        row['Birthday'] = '1990-06-15'
        result = mc.parse_csv_row(row)
        self.assertEqual(result['bday'], '1990-06-15')

    def test_notes_preserved(self):
        row = self._base_row()
        row['First Name'] = 'Notes'
        row['Phone 1 - Value'] = '+15550005555'
        row['Notes'] = 'A test note'
        result = mc.parse_csv_row(row)
        self.assertEqual(result['notes'], 'A test note')

    def test_prefix_suffix_nickname_preserved(self):
        row = self._base_row()
        row['First Name'] = 'Alice'
        row['Phone 1 - Value'] = '+15550006666'
        row['Name Prefix'] = 'Dr.'
        row['Name Suffix'] = 'PhD'
        row['Nickname'] = 'Al'
        result = mc.parse_csv_row(row)
        self.assertEqual(result['prefix'], 'Dr.')
        self.assertEqual(result['suffix'], 'PhD')
        self.assertEqual(result['nickname'], 'Al')

    def test_org_title_department_preserved(self):
        row = self._base_row()
        row['First Name'] = 'Emp'
        row['Phone 1 - Value'] = '+15550007777'
        row['Organization Name'] = 'BigCorp'
        row['Organization Title'] = 'Director'
        row['Organization Department'] = 'Engineering'
        result = mc.parse_csv_row(row)
        self.assertEqual(result['org'], 'BigCorp')
        self.assertEqual(result['title'], 'Director')
        self.assertEqual(result['department'], 'Engineering')

    def test_emails_stored_as_tuples_with_label(self):
        row = self._base_row()
        row['First Name'] = 'Emi'
        row['Phone 1 - Value'] = '+15550008888'
        row['E-mail 1 - Label'] = 'Work'
        row['E-mail 1 - Value'] = 'work@example.com'
        result = mc.parse_csv_row(row)
        self.assertEqual(len(result['emails']), 1)
        self.assertEqual(result['emails'][0][0], 'work@example.com')
        self.assertEqual(result['emails'][0][1], 'Work')


# ---------------------------------------------------------------------------
# Unit: vCard file parsing
# ---------------------------------------------------------------------------

class TestParseVcfFile(unittest.TestCase):
    def setUp(self):
        self._tmp_paths: list[str] = []

    def _write_vcf(self, content: str) -> Path:
        path = _write_vcf(content)
        self._tmp_paths.append(str(path))
        return path

    def tearDown(self):
        for path in self._tmp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_phone_contact_parsed(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:Bob Builder\r\n'
            'TEL;TYPE=CELL:+15552223333\r\n'
            'END:VCARD\r\n'
        )
        contacts, kept, dropped = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 1)
        self.assertEqual(dropped, 0)
        self.assertEqual(contacts[0]['display'], 'Bob Builder')
        self.assertEqual(contacts[0]['phones'][0][0], '5552223333')

    def test_phone_less_contact_dropped(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:Email Only\r\n'
            'EMAIL;TYPE=INTERNET:eo@example.com\r\n'
            'END:VCARD\r\n'
        )
        contacts, kept, dropped = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 0)
        self.assertEqual(dropped, 1)

    def test_n_field_populates_name_parts(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'N:Smith;John;M;;\r\n'
            'TEL;TYPE=CELL:+15551112222\r\n'
            'END:VCARD\r\n'
        )
        contacts, _, _ = mc.parse_vcf_file(vcf)
        self.assertEqual(contacts[0]['last'], 'Smith')
        self.assertEqual(contacts[0]['first'], 'John')
        self.assertEqual(contacts[0]['middle'], 'M')

    def test_fn_takes_priority_over_n_for_display(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:John M. Smith\r\n'
            'N:Smith;John;M;;\r\n'
            'TEL;TYPE=CELL:+15551112222\r\n'
            'END:VCARD\r\n'
        )
        contacts, _, _ = mc.parse_vcf_file(vcf)
        self.assertEqual(contacts[0]['display'], 'John M. Smith')

    def test_org_parsed(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:Acme Corp\r\n'
            'ORG:Acme Corp;Engineering\r\n'
            'TEL;TYPE=WORK:+15554445555\r\n'
            'END:VCARD\r\n'
        )
        contacts, _, _ = mc.parse_vcf_file(vcf)
        self.assertEqual(contacts[0]['org'], 'Acme Corp')
        self.assertEqual(contacts[0]['department'], 'Engineering')

    def test_vcard4_parsed(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:4.0\r\n'
            'FN:Alex Kim\r\n'
            'TEL;TYPE=cell:+15550001234\r\n'
            'END:VCARD\r\n'
        )
        contacts, kept, _ = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 1)
        self.assertEqual(contacts[0]['phones'][0][0], '5550001234')

    def test_line_folding_handled(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:Folded\r\n'
            'TEL;TYPE=CELL:+1555\r\n'
            ' 1112222\r\n'
            'END:VCARD\r\n'
        )
        contacts, kept, _ = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 1)
        self.assertEqual(contacts[0]['phones'][0][0], '5551112222')

    def test_multiple_cards_in_one_file(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A\r\nTEL;TYPE=CELL:+15551111111\r\nEND:VCARD\r\n'
            'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:B\r\nEMAIL:b@x.com\r\nEND:VCARD\r\n'
            'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:C\r\nTEL;TYPE=CELL:+15552222222\r\nEND:VCARD\r\n'
        )
        contacts, kept, dropped = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 2)
        self.assertEqual(dropped, 1)

    def test_escape_sequences_unescaped_in_values(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:O\'Brien\\, Inc.\r\n'
            'TEL;TYPE=CELL:+15559990000\r\n'
            'END:VCARD\r\n'
        )
        contacts, _, _ = mc.parse_vcf_file(vcf)
        self.assertIn(',', contacts[0]['display'])

    def test_item_group_tel_kept(self):
        # Real iCloud exports put the only phone on an item-grouped TEL line.
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:Marie Tremblay\r\n'
            'item1.TEL;type=CELL;type=pref:+1 (555) 200-3000\r\n'
            'item1.X-ABLabel:_$!<Mobile>!$_\r\n'
            'END:VCARD\r\n'
        )
        contacts, kept, dropped = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 1)
        self.assertEqual(dropped, 0)
        self.assertEqual(contacts[0]['phones'][0][0], '5552003000')

    def test_tel_uri_scheme_stripped(self):
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:4.0\r\n'
            'FN:Uri Person\r\n'
            'TEL;TYPE=voice,cell;VALUE=uri:tel:+15552005000\r\n'
            'END:VCARD\r\n'
        )
        contacts, kept, _ = mc.parse_vcf_file(vcf)
        self.assertEqual(kept, 1)
        raw = contacts[0]['phones'][0][1]
        self.assertNotIn('tel:', raw.lower())
        self.assertEqual(contacts[0]['phones'][0][0], '5552005000')

    def test_raw_vcard_block_stored(self):
        # iCloud passthrough: raw block must be captured
        vcf = self._write_vcf(
            'BEGIN:VCARD\r\nVERSION:3.0\r\n'
            'FN:Raw Card\r\n'
            'TEL;TYPE=CELL:+15551234567\r\n'
            'PHOTO;ENCODING=b;TYPE=JPEG:abc123==\r\n'
            'END:VCARD\r\n'
        )
        contacts, _, _ = mc.parse_vcf_file(vcf)
        self.assertIn('PHOTO', contacts[0]['_raw_vcard'])
        self.assertIn('BEGIN:VCARD', contacts[0]['_raw_vcard'])


# ---------------------------------------------------------------------------
# Unit: dedup / merge logic
# ---------------------------------------------------------------------------

class TestMergeContacts(unittest.TestCase):
    def _make_contact(
        self, name: str, phones: list[str],
        emails: list[str] | None = None,
        source: str = 'google',
    ) -> dict:
        norms_raw = [(mc.normalize_phone(p), p, 'Mobile') for p in phones]
        email_tuples = [(e, '') for e in (emails or [])]
        c = mc._empty_contact()
        c.update({
            'display': name, 'first': name,
            'phones': norms_raw, 'emails': email_tuples,
            '_source': source,
        })
        return c

    def test_same_phone_merges_to_one_card(self):
        a = self._make_contact('Alice A', ['+15551112222'])
        b = self._make_contact('Alice B', ['+15551112222'])
        merged = mc.merge_contacts([a, b])
        self.assertEqual(len(merged), 1)

    def test_different_phones_stay_separate(self):
        a = self._make_contact('Alice', ['+15551112222'])
        b = self._make_contact('Bob', ['+15553334444'])
        merged = mc.merge_contacts([a, b])
        self.assertEqual(len(merged), 2)

    def test_merged_card_unions_phones(self):
        a = self._make_contact('Alice', ['+15551112222'])
        b = self._make_contact('Alice', ['+15551112222', '+15555556666'])
        merged = mc.merge_contacts([a, b])
        self.assertEqual(len(merged), 1)
        norms = {p[0] for p in merged[0]['phones']}
        self.assertIn('5551112222', norms)
        self.assertIn('5555556666', norms)

    def test_merged_card_unions_emails(self):
        a = self._make_contact('Alice', ['+15551112222'], ['a@x.com'])
        b = self._make_contact('Alice', ['+15551112222'], ['b@x.com'])
        merged = mc.merge_contacts([a, b])
        self.assertEqual(len(merged), 1)
        addrs = [e[0] for e in merged[0]['emails']]
        self.assertIn('a@x.com', addrs)
        self.assertIn('b@x.com', addrs)

    def test_duplicate_count_increments(self):
        a = self._make_contact('Alice', ['+15551112222'])
        b = self._make_contact('Alice', ['+15551112222'])
        merged = mc.merge_contacts([a, b])
        self.assertEqual(merged[0]['_merged_count'], 1)

    def test_csv_and_vcf_contacts_merge_by_phone(self):
        csv_contact = self._make_contact('Jane Doe', ['+15551234567'], ['jane@csv.com'])
        vcf_contact = self._make_contact('Jane D.', ['+15551234567'], ['jane@vcf.com'])
        merged = mc.merge_contacts([csv_contact, vcf_contact])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['display'], 'Jane Doe')
        addrs = [e[0] for e in merged[0]['emails']]
        self.assertIn('jane@csv.com', addrs)
        self.assertIn('jane@vcf.com', addrs)

    def test_merge_unions_addresses(self):
        a = self._make_contact('Alice', ['+15551112222'])
        a['addresses'] = [{'label': 'Home', 'street': '1 Main St', 'city': 'Town',
                           'formatted': '', 'pobox': '', 'region': '', 'postal': '', 'country': '', 'extended': ''}]
        b = self._make_contact('Alice', ['+15551112222'])
        b['addresses'] = [{'label': 'Work', 'street': '2 Work Ave', 'city': 'City',
                           'formatted': '', 'pobox': '', 'region': '', 'postal': '', 'country': '', 'extended': ''}]
        merged = mc.merge_contacts([a, b])
        self.assertEqual(len(merged[0]['addresses']), 2)

    def test_merge_prefers_icloud_raw_block(self):
        google = self._make_contact('Jane', ['+15551234567'])
        icloud = self._make_contact('Jane', ['+15551234567'], source='icloud')
        icloud['_raw_vcard'] = 'BEGIN:VCARD\r\nEND:VCARD\r\n'
        merged = mc.merge_contacts([google, icloud])
        self.assertEqual(len(merged), 1)
        # After merge, the raw block should be preserved
        self.assertIn('BEGIN:VCARD', merged[0]['_raw_vcard'])


# ---------------------------------------------------------------------------
# Unit: render_vcard — lossless + filter flags
# ---------------------------------------------------------------------------

class TestRenderVcard(unittest.TestCase):
    def _make_full_card(self) -> dict:
        """A fully-populated contact dict for render tests."""
        c = mc._empty_contact()
        c.update({
            'display': 'Jane Doe', 'first': 'Jane', 'last': 'Doe',
            'middle': '', 'prefix': 'Dr.', 'suffix': '', 'nickname': 'JD',
            'org': 'Acme Corp', 'department': 'Research', 'title': 'Engineer',
            'phones': [
                ('5551234567', '+15551234567', 'Mobile'),
                ('5559870000', '+15559870000', 'Work'),
                ('5550001111', '+15550001111', 'iPhone'),
            ],
            'emails': [('jane@example.com', 'Work'), ('j@personal.com', '')],
            'addresses': [{
                'label': 'Home', 'formatted': '', 'street': '123 Main St',
                'city': 'Toronto', 'pobox': '', 'region': 'ON',
                'postal': 'M5V 0A1', 'country': 'Canada', 'extended': '',
            }],
            'urls': [
                ('Profile', 'https://jane.example.com'),
                ('LinkedIn', 'https://linkedin.com/in/janedoe'),
            ],
            'bday': '1985-04-12',
            'notes': 'A test note',
            'photo_url': 'https://example.com/photo.jpg',
            'labels': ['Friends'],
            'relations': [('Spouse', 'Partner A')],
            'custom_fields': [('Favourite Colour', 'Blue')],
            'extra_vcards': [],
            '_source': 'google',
            '_raw_vcard': '',
            'phone_norms': {'5551234567', '5559870000', '5550001111'},
            '_merged_count': 0,
        })
        return c

    def test_phone_work_label_renders_type_work(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('TEL;TYPE=WORK', vcf)

    def test_phone_iphone_label_renders_item_group(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        # item-group lines for iPhone
        self.assertRegex(vcf, r'item\d+\.TEL;type=CELL')
        self.assertRegex(vcf, r'item\d+\.X-ABLabel:iPhone')

    def test_address_renders_adr(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('ADR', vcf)
        self.assertIn('123 Main St', vcf)
        self.assertIn('Toronto', vcf)

    def test_bday_renders(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('BDAY:1985-04-12', vcf)

    def test_note_renders(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('NOTE:A test note', vcf)

    def test_nickname_renders(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('NICKNAME:JD', vcf)

    def test_title_renders(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('TITLE:Engineer', vcf)

    def test_org_includes_department(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('ORG:Acme Corp;Research', vcf)

    def test_n_includes_prefix_suffix(self):
        card = self._make_full_card()
        vcf = mc.render_vcard(card)
        self.assertIn('Dr.', vcf)


# ---------------------------------------------------------------------------
# Unit: iCloud passthrough rendering
# ---------------------------------------------------------------------------

class TestIcloudPassthrough(unittest.TestCase):
    def _icloud_card(self, raw_block: str) -> dict:
        """Build a minimal iCloud card dict with a raw block."""
        c = mc._empty_contact()
        c['_source'] = 'icloud'
        c['_raw_vcard'] = raw_block
        c['phones'] = [('5551234567', '+15551234567', 'CELL')]
        c['phone_norms'] = {'5551234567'}
        c['_merged_count'] = 0
        c['display'] = 'Test Person'
        return c

    def _raw(self) -> str:
        return (
            'BEGIN:VCARD\r\n'
            'VERSION:3.0\r\n'
            'FN:Test Person\r\n'
            'TEL;TYPE=CELL:+15551234567\r\n'
            'PHOTO;ENCODING=b;TYPE=JPEG:abc123==\r\n'
            'X-SOCIALPROFILE;type=linkedin:https://linkedin.com/in/test\r\n'
            'EMAIL;TYPE=INTERNET:test@example.com\r\n'
            'END:VCARD\r\n'
        )

    def test_passthrough_preserves_photo_by_default(self):
        card = self._icloud_card(self._raw())
        vcf = mc.render_vcard(card)
        self.assertIn('PHOTO', vcf)

    def test_passthrough_item_group_tel_preserved(self):
        raw = (
            'BEGIN:VCARD\r\n'
            'VERSION:3.0\r\n'
            'FN:Grouped\r\n'
            'item1.TEL;type=CELL:+15551234567\r\n'
            'item1.X-ABLabel:iPhone\r\n'
            'END:VCARD\r\n'
        )
        card = self._icloud_card(raw)
        vcf = mc.render_vcard(card)
        self.assertIn('item1.TEL', vcf)
        self.assertIn('item1.X-ABLabel:iPhone', vcf)


# ---------------------------------------------------------------------------
# Integration: full pipeline against the fixtures in tests/fixtures/
# ---------------------------------------------------------------------------

class TestIntegrationSampleData(unittest.TestCase):
    """Run the full pipeline against the anonymized fixtures in tests/fixtures/."""

    def setUp(self):
        self.tmp_output = tempfile.NamedTemporaryFile(suffix='.vcf', delete=False)
        self.tmp_output.close()

    def tearDown(self):
        os.unlink(self.tmp_output.name)

    def _run_pipeline(self, extra_args: list[str] | None = None) -> tuple[str, str]:
        """Run merge_contacts via subprocess; return (stdout, output_vcf_content)."""
        cmd = [
            sys.executable,
            str(REPO_ROOT / 'merge_contacts.py'),
            '--input', str(FIXTURE_DIR),
            '--output', self.tmp_output.name,
        ]
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        content = Path(self.tmp_output.name).read_text(encoding='utf-8')
        return result.stdout, content

    def test_phone_less_contacts_dropped(self):
        stdout, _ = self._run_pipeline()
        count = _parse_report_line(stdout, 'Phone-less dropped')
        self.assertIsNotNone(count)
        # Email Only Person (CSV), newsletter@example.com (CSV),
        # iCloud Only Person (VCF) — at least 3 dropped
        self.assertGreaterEqual(count, 3)

    def test_phone_bearing_contacts_kept(self):
        stdout, _ = self._run_pipeline()
        count = _parse_report_line(stdout, 'Phone-bearing kept')
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, 8)

    def test_cross_file_dedup_merges_jane_doe(self):
        stdout, _ = self._run_pipeline()
        count = _parse_report_line(stdout, 'Duplicate cards merged')
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, 2)

    def test_vcf_and_csv_contact_shares_phone_merges(self):
        stdout, _ = self._run_pipeline()
        kept = _parse_report_line(stdout, 'Phone-bearing kept')
        final = _parse_report_line(stdout, 'Final unique contacts')
        self.assertIsNotNone(kept)
        self.assertIsNotNone(final)
        self.assertLess(final, kept)

    def test_output_vcf_structure(self):
        _, content = self._run_pipeline()
        self.assertIn('BEGIN:VCARD', content)
        self.assertIn('VERSION:3.0', content)
        self.assertIn('END:VCARD', content)
        self.assertIn('TEL;TYPE=', content)

    def test_final_unique_contacts_reasonable_range(self):
        stdout, _ = self._run_pipeline()
        count = _parse_report_line(stdout, 'Final unique contacts')
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, 6)
        self.assertLessEqual(count, 10)

    def test_distinct_phone_numbers_count(self):
        stdout, _ = self._run_pipeline()
        count = _parse_report_line(stdout, 'Distinct phone numbers')
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, 8)

    def test_emails_kept_by_default(self):
        """The clean default keeps email addresses."""
        _, content = self._run_pipeline()
        self.assertIn('EMAIL', content)

    def test_vcf_files_counted_in_report(self):
        stdout, _ = self._run_pipeline()
        count = _parse_report_line(stdout, 'vCard files processed')
        self.assertIsNotNone(count)
        self.assertGreaterEqual(count, 1)

    def test_icloud_photo_preserved_by_default(self):
        """Real embedded PHOTO from an iCloud passthrough must survive."""
        _, content = self._run_pipeline()
        self.assertIn('PHOTO', content)

    def test_icloud_social_profile_dropped_by_default(self):
        """X-SOCIALPROFILE is not a kept native field — dropped by default."""
        _, content = self._run_pipeline()
        self.assertNotIn('X-SOCIALPROFILE', content)

    def test_output_contains_bday_for_jane(self):
        """account-a.csv has Jane with Birthday=1985-04-12 — kept."""
        _, content = self._run_pipeline()
        self.assertIn('BDAY', content)

    def test_output_contains_adr_for_jane(self):
        """account-a.csv has Jane with an address — kept."""
        _, content = self._run_pipeline()
        self.assertIn('ADR', content)

    def test_output_contains_title_for_jane(self):
        """Job Title is kept as its own native field, not folded into ORG."""
        _, content = self._run_pipeline()
        self.assertIn('TITLE:', content)

    def test_url_dropped_by_default(self):
        """Websites are not a native target field — dropped by default."""
        _, content = self._run_pipeline()
        self.assertNotIn('URL', content)

    def test_custom_field_dropped_by_default(self):
        """Google custom fields (X-GOOGLE-*) are dropped by default."""
        _, content = self._run_pipeline()
        self.assertNotIn('X-GOOGLE-', content)

    def test_categories_dropped_by_default(self):
        """Google group labels (CATEGORIES) are dropped by default."""
        _, content = self._run_pipeline()
        self.assertNotIn('CATEGORIES', content)

    def test_company_only_contact_gets_x_abshowas(self):
        """A contact with a company but no person name files as a company."""
        _, content = self._run_pipeline()
        self.assertIn('X-ABShowAs:COMPANY', content)

    def test_field_manifest_printed(self):
        """The field manifest section should appear in stdout."""
        stdout, _ = self._run_pipeline()
        self.assertIn('Field Manifest', stdout)
        self.assertIn('TEL (phones)', stdout)

    def test_iphone_label_in_csv_renders_item_group_in_output(self):
        """Carlos in account-b.csv has label 'iPhone' → should produce item-group TEL."""
        _, content = self._run_pipeline()
        # Check for item-group X-ABLabel:iPhone pattern
        self.assertRegex(content, r'item\d+\.X-ABLabel:iPhone')


# ---------------------------------------------------------------------------
# Unit: RFC 6350 line folding (_fold_vcard_line)
# ---------------------------------------------------------------------------

class TestFoldVcardLine(unittest.TestCase):
    def test_short_line_unchanged(self):
        line = 'NOTE:short'
        self.assertEqual(mc._fold_vcard_line(line), line)

    def test_exactly_75_octets_unchanged(self):
        # 5 chars for 'NOTE:' + 70 'a' = 75 octets total
        line = 'NOTE:' + 'a' * 70
        self.assertEqual(mc._fold_vcard_line(line), line)

    def test_long_line_folds_and_round_trips(self):
        # Build a NOTE value that exceeds 75 octets when combined with the property name
        long_value = 'This is a deliberately long note value that will exceed seventy-five octets and must be folded.'
        line = f'NOTE:{long_value}'
        folded = mc._fold_vcard_line(line)
        # Folded output must contain CRLF continuation
        self.assertIn('\r\n', folded)
        # Every physical line after the first must start with a space
        physical_lines = folded.split('\r\n')
        for physical in physical_lines[1:]:
            self.assertTrue(physical.startswith(' '), f'Continuation line missing leading space: {physical!r}')
        # Round-trip: unfolding the folded result must reconstruct the original logical line
        unfolded_lines = mc._unfold_vcard_lines(folded)
        self.assertEqual(len(unfolded_lines), 1)
        self.assertEqual(unfolded_lines[0], line)

    def test_multibyte_utf8_not_split(self):
        # 'é' is 2 UTF-8 bytes; build a value that places it right at a fold boundary
        # 'NOTE:' is 5 bytes; fill with 69 ASCII chars so the next byte is the first of 'é'
        line = 'NOTE:' + 'a' * 69 + 'é' + 'b' * 10
        folded = mc._fold_vcard_line(line)
        # Each physical line, when decoded as UTF-8, must be valid (no split char)
        for physical in folded.split('\r\n'):
            physical.encode('utf-8')  # raises UnicodeEncodeError if somehow broken


# ---------------------------------------------------------------------------
# Unit: merge_contacts — order-dependent single-pass behavior
# ---------------------------------------------------------------------------

class TestMergeContactsSinglePassBehavior(unittest.TestCase):
    def _make_contact(self, name: str, phones: list[str]) -> dict:
        c = mc._empty_contact()
        c['display'] = name
        c['first'] = name
        c['_source'] = 'google'
        for raw in phones:
            norm = mc.normalize_phone(raw)
            c['phones'].append((norm, raw, 'Mobile'))
        return c

    def test_order_bridge_after_both_produces_two_cards(self):
        """A(phone1) + C(phone2) + B(phone1+phone2) → 2 cards (bridge B arrives last).

        A and C are placed first with no shared number; when B arrives it
        shares phone1 with A's already-canonical card, so B merges into A.
        A's card now has phone1+phone2.  C's card stays separate because C
        was seen before A's card registered phone2 in the index.
        """
        a = self._make_contact('A', ['+15550000001'])
        c = self._make_contact('C', ['+15550000002'])
        b = self._make_contact('B', ['+15550000001', '+15550000002'])
        result = mc.merge_contacts([a, c, b])
        self.assertEqual(len(result), 2)

    def test_order_bridge_before_both_also_produces_two_cards(self):
        """A(phone1) + B(phone1+phone2) + C(phone2) → 2 cards (single-pass greedy).

        B merges into A because they share phone1.  _merge_two_contacts adds
        phone2 to A's 'phone_norms' set before the outer loop can register
        phone2 → A's index, so the registration check sees phone2 already in
        phone_norms and skips the phone_to_idx update.  C therefore creates a
        new card.  This is the documented single-pass greedy behavior: a bridge
        contact does not reliably collapse a transitive triple into one card —
        you get a near-duplicate either way (Apple's Look for Duplicates fixes
        it post-import).
        """
        a = self._make_contact('A', ['+15550000001'])
        b = self._make_contact('B', ['+15550000001', '+15550000002'])
        c = self._make_contact('C', ['+15550000002'])
        result = mc.merge_contacts([a, b, c])
        # Greedy single-pass: A+B merge (share phone1), but phone2 is not
        # registered in the index in time for C → C becomes its own card.
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# Unit: FieldManifest._kept_str — zero-seen edge case
# ---------------------------------------------------------------------------

class TestFieldManifestZeroSeen(unittest.TestCase):
    """The _kept_str helper must not claim 'all kept' when seen == 0."""

    def _capture_print_report(self, manifest: mc.FieldManifest) -> str:
        """Capture stdout from manifest.print_report()."""
        import io
        import unittest.mock
        buf = io.StringIO()
        with unittest.mock.patch('sys.stdout', buf):
            manifest.print_report()
        return buf.getvalue()

    def test_zero_seen_does_not_say_all_kept(self):
        """When seen == 0 (e.g. CATEGORIES or Photo URL on a fixture with none),
        the report must NOT emit '0 seen, all kept' — that claim is misleading
        for fields that are always dropped and meaningless for truly empty ones.
        """
        # A fresh FieldManifest has all counts at 0, which exercises every
        # zero-seen row: CATEGORIES, Photo (Google URL), Extra vCard lines, etc.
        manifest = mc.FieldManifest()
        output = self._capture_print_report(manifest)
        self.assertNotIn('0 seen, all kept', output,
                         "zero-seen rows must not claim 'all kept'")


if __name__ == '__main__':
    unittest.main()

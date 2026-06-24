# Contributing

This is an intentionally opinionated tool. PRs that change which fields are kept or dropped will be declined — `render_vcard()` is the single place the field policy lives, and forking the repo to adapt it is the intended path for anyone who wants a different set of fields.

Bug fixes, test coverage improvements, and Python-version compatibility fixes are welcome. All tests use anonymized fixtures only (`tests/fixtures/` — fake `555-xxxx` numbers, placeholder names); never add real contact data to the test suite.

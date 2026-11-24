# Smart Data Models conformance

The platform uses the official [Smart Data Models](https://smartdatamodels.org) entity types
listed in `config/entity-types/allowed.txt`. This folder is what CI uses to check that the
entities the platform builds really conform to them.

- `pins.json` names every official file the check needs (JSON Schemas, the shared schemas they
  refer to, the subject contexts and the NGSI-LD core context) by repository, commit and sha256.
  The files themselves are not copied here: the checker downloads them and refuses any file
  whose checksum differs. `gaps` records what the official files are known to lack.
- `pending.txt` lists the official types that have no validated example yet. It may only lose
  lines.
- `deviations.json` records, one by one, every official type or attribute that the platform
  context maps to another IRI than the official one. It is generated (`deviations --write`),
  checked on every pull request, and may only lose entries. An official attribute the context
  does not declare at all is not a deviation: it is reported when an example carries it. Smart
  Data Models gives some names a different IRI in each subject and a context maps a name once: an
  entry that lists `officialFor` is official for those types and cannot be avoided, only moved.
- `../sdm-examples/` holds one example entity per file, written by the builder that produces
  that entity in production. A test next to each builder keeps the file equal to its output.

The checker is `.github/scripts/check_sdm_conformance.py`:

```
python3 .github/scripts/check_sdm_conformance.py examples config/sdm-examples
```

`examples` reports a type mapped to a non-official IRI, an entity that breaks the official
schema, and an attribute that is not a term of the platform context (the broker would store it under the
default vocabulary). Examples must be in the NGSI-LD normalized form. Of the JSON Schema formats
only `date-time` and `date` are enforced.

`deviations` checks `deviations.json` against the context, and `ratchet --base <ref>` checks that
neither it nor `pending.txt` grew since that ref. A type that joins `allowed.txt` in the same
change may bring the deviations of the terms it shares with the other types.

To add an official type: add it to `allowed.txt`, pin its schema in `pins.json`, and either add
an example or list it in `pending.txt`. To move a pin, change the commit and the checksums in
the same pull request and say why.

`upstream` compares every pinned file with the one its repository serves today. It runs weekly
(`.github/workflows/sdm-upstream-watch.yml`) and on demand. A failed run that lists files means
an official model changed since its pin, not that anything here broke: read the change it links
to, then move the pin (a new official attribute may replace one of the platform's own terms). A
failed run with an internal error means the official repositories could not be read; run it again.

## Modules

A module repository that calls the shared workflow `_public-repo-hygiene.yml` gets the same
validation for its own entities: put one example per file in `sdm-examples/` at the root of the
module, each written by the builder that produces that entity (with a test that keeps the file
equal to the builder's output). A type whose examples do not conform yet may be listed in
`sdm-examples/pending.txt`. Findings are reported as warnings and do not fail the module's build
until the caller sets `sdm_conformance_enforce: true`.

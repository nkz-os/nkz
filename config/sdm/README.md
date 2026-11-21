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
- `../sdm-examples/` holds one example entity per file, written by the builder that produces
  that entity in production. A test next to each builder keeps the file equal to its output.

The checker is `.github/scripts/check_sdm_conformance.py`:

```
python3 .github/scripts/check_sdm_conformance.py examples config/sdm-examples
```

It reports a type mapped to a non-official IRI, an entity that breaks the official schema, and
an attribute that is not a term of the platform context (the broker would store it under the
default vocabulary). Examples must be in the NGSI-LD normalized form. Of the JSON Schema formats
only `date-time` and `date` are enforced.

To add an official type: add it to `allowed.txt`, pin its schema in `pins.json`, and either add
an example or list it in `pending.txt`. To move a pin, change the commit and the checksums in
the same pull request and say why.

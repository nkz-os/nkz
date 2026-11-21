# Satellite observation fixtures

- `legacy_stored.json`: `EOProduct` entities in the earlier shape, as a broker returns them in the
  normalized representation, one per stored variant. Identifiers are synthetic.
- `sdm_stored.json`: `EOSatelliteImagery` entities as a broker returns them, including the
  one-element `analytics` list that comes back as a bare object.
- `expected.json`: what the reader returns for both files together. The Python and TypeScript
  readers are tested against the same file.

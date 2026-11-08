---
name: repair-validation
description: Engineering-only isolated repair acceptance fixture. Normalize a CSV molecule table without changing scientific values. Not a scientific method.
---
# Engineering repair validation fixture
Use only when a user explicitly requests a repair acceptance test. `normalize_table` reads CSV with a SMILES header and writes normalized CSV with exactly the same rows and values. Required --input and --output. This is an engineering acceptance fixture, not a production scientific operation. CSV content and molecule identities are checked by the input reviewer; runtime engineering failures are handled by the diagnostician. No descriptor calculation or scientific claims.

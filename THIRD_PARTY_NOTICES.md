# Third-party attribution

Per-Skill origins, revisions, licenses and copyrights are retained in
`skill_provenance.json`. Source instructions and wrappers were adapted for
independent package discovery and explicit execution. Original file notices
remain in their source files.

- Synthetic Sciences OpenScience: Apache-2.0; `third_party/OPENSCIENCE_NOTICE.txt`.
- Google Science Skills: Apache-2.0 software and CC-BY-4.0 materials; source terms
  retained in `third_party/GOOGLE_SKILL_TERMS.md`; attribution and upstream
  revisions appear in the registry.
- Adaptyv Protein Design Skills: MIT; `third_party/licenses/Adaptyv-MIT.txt`.
- Microsoft MarkItDown: bundled Skill notice at `skills/utilities/markitdown/LICENSE.txt`.
- LigandMPNN: MIT (Copyright (c) 2024 Justas Dauparas); bundled source at
  `enzyme_core/vendor/ligandmpnn` with its `LICENSE`. Its `openfold/` subtree is
  Apache-2.0 (AlQuraishi Laboratory; DeepMind) and retains that license text.
- mut_model: MIT (Copyright (c) 2025 ekko); bundled source at
  `enzyme_core/vendor/mut_model` with its `LICENSE`.

Per-Skill provenance was assembled by the project maintainers from the upstream
sources named in `skill_provenance.json`. Notice files retained here are
unchanged copies of the corresponding upstream notices. The only bundled
third-party source is under `enzyme_core/vendor/`; model weights, datasets,
Docker images, external binaries, credentials, remote database contents and
publications are not bundled and retain their own terms. No license for external
scientific assets is implied.

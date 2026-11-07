# Bundled Skill sources

This directory ships the source code that several Skills execute locally, so a
published checkout of this repository is self-contained at the code level. Only
source is bundled; model checkpoints, experimental datasets, Docker images, and
external binaries stay outside and are declared in `runtime/resources.json`.

| Directory | Upstream | License | User-provided assets |
| --- | --- | --- | --- |
| `ligandmpnn/` | [LigandMPNN](https://github.com/dauparas/LigandMPNN) | MIT; `openfold/` is Apache-2.0 | `LIGANDMPNN_MODEL_PARAMS` / `PROTEINMPNN_MODEL_PARAMS` checkpoints |
| `mut_model/` | local mut_model project | MIT | `data/`, `outputs/`, `pretrain_weight/`, `finetune_weight/` |

Paths in `runtime/resources.json` use `${ENZYME_CORE_VENDOR}`, which resolves to
this directory at runtime (see `enzyme_core/support/config.py`). Weights and data
are supplied through `resources.local.json` or the documented environment
variables; the bundled sources are not modified.

Deliberately not bundled:

- GEMME runs from its official Docker image (`elodielaine/gemme:gemme`); the
  Skill needs no source checkout.
- VenusFSFP is not bundled because its local copy is licensed
  CC BY-NC-ND 4.0. Obtain it separately and set `VENUSFSFP_HOME`.
- PLIP, CAVER, CP2K, Foldseek, Docker images, and model checkpoints remain
  external tools or assets with their own terms.

Files are copied unchanged from the local checkouts; only VCS metadata, bytecode
caches, and (for `mut_model/`) datasets and weight directories were excluded.
Upstream license texts are retained in each directory.

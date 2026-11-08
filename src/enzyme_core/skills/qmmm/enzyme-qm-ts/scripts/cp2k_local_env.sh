#!/usr/bin/env bash
# Source this file to select the verified local CP2K 2026.2 CPU/GPU build.
#
# No container is involved: both variants are local builds under
# ${ENZYME_DATA_ROOT}/cp2k-main.  The manifest declares them in runtime_binaries
# (cp2k_gpu = preferred, cp2k_cpu = fallback); see references/deployment.md.

# Resolve the executable for a variant.  <VARIANT>_BIN wins, then
# <VARIANT>_ROOT (install directory), then the verified local default.
cp2k_binary_for() {
  case "${1:-}" in
    cpu) printf '%s\n' "${CP2K_CPU_BIN:-${CP2K_CPU_ROOT:-${ENZYME_DATA_ROOT}/cp2k-main/cpu/install}/bin/cp2k.psmp}" ;;
    gpu) printf '%s\n' "${CP2K_GPU_BIN:-${CP2K_GPU_ROOT:-${ENZYME_DATA_ROOT}/cp2k-main/gpu/install}/bin/cp2k.psmp}" ;;
    *) echo "ERROR: CP2K variant must be cpu or gpu" >&2; return 2 ;;
  esac
}

# Probe CUDA acceleration.  Prints cuda_available, or the concrete reason it is
# unusable: gpu_build_missing, nvidia_smi_missing, no_visible_device.
cp2k_gpu_availability() {
  local binary
  binary="$(cp2k_binary_for gpu)" || return 2
  [ -x "$binary" ] || { printf 'gpu_build_missing\n'; return 0; }
  command -v nvidia-smi >/dev/null 2>&1 || { printf 'nvidia_smi_missing\n'; return 0; }
  nvidia-smi -L 2>/dev/null | grep -q '^GPU ' || { printf 'no_visible_device\n'; return 0; }
  printf 'cuda_available\n'
}

# Resolve CP2K_VARIANT (auto|gpu|cpu).  auto prefers the GPU build and degrades
# to the CPU build when CUDA acceleration is unavailable.
cp2k_select_variant() {
  case "${CP2K_VARIANT:-auto}" in
    gpu|cpu) printf '%s\n' "$CP2K_VARIANT" ;;
    auto)
      if [ "$(cp2k_gpu_availability)" = "cuda_available" ]; then
        printf 'gpu\n'
      else
        printf 'cpu\n'
      fi
      ;;
    *)
      echo "ERROR: CP2K_VARIANT must be auto, gpu or cpu" >&2
      return 2
      ;;
  esac
}

cp2k_local_activate() {
  local variant="${1:-auto}"
  local root setup binary
  case "$variant" in
    auto) variant="$(cp2k_select_variant)" || return $? ;;
  esac
  case "$variant" in
    cpu)
      root="${CP2K_CPU_ROOT:-${ENZYME_DATA_ROOT}/cp2k-main/cpu/install}"
      setup="${CP2K_CPU_SETUP:-${ENZYME_DATA_ROOT}/cp2k-main/cpu/source/tools/toolchain/install/setup}"
      ;;
    gpu)
      root="${CP2K_GPU_ROOT:-${ENZYME_DATA_ROOT}/cp2k-main/gpu/install}"
      setup="${CP2K_GPU_SETUP:-${ENZYME_DATA_ROOT}/cp2k-main/gpu/source-conda/tools/toolchain/install/setup}"
      ;;
    *)
      echo "ERROR: CP2K variant must be auto, cpu or gpu" >&2
      return 2
      ;;
  esac
  binary="$(cp2k_binary_for "$variant")" || return $?

  [ -x "$binary" ] || {
    echo "ERROR: local CP2K binary not executable: $binary" >&2
    return 3
  }
  [ -f "$setup" ] || {
    echo "ERROR: CP2K toolchain setup not found: $setup" >&2
    return 3
  }

  # The toolchain setup supplies ELPA/DBCSR and, for GPU, the private CUDA view.
  # Sourcing occurs inside the launcher process and does not mutate conda agent.
  set +u
  # shellcheck disable=SC1090
  source "$setup"
  set -u
  export LD_LIBRARY_PATH="$root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  if [ "$variant" = gpu ]; then
    export CUDA_HOME="${CUDA_HOME:-${ENZYME_DATA_ROOT}/cuda-12.8}"
    export CUDA_PATH="$CUDA_HOME"
    export PATH="$CUDA_HOME/bin${PATH:+:$PATH}"
    export LD_LIBRARY_PATH="$CUDA_HOME/lib64:$LD_LIBRARY_PATH"
    # The CUDA 12.8 runfile inherited a conda host compiler at install time;
    # force the system compiler for reproducible launches.
    export NVCC_PREPEND_FLAGS="${NVCC_PREPEND_FLAGS:--ccbin=/usr/bin/g++}"
  fi
  if [ "$variant" = gpu ]; then
    export CP2K_DATA_DIR="${CP2K_GPU_DATA_DIR:-${ENZYME_DATA_ROOT}/cp2k-main/gpu/source-conda/data}"
  else
    export CP2K_DATA_DIR="${CP2K_CPU_DATA_DIR:-${ENZYME_DATA_ROOT}/cp2k-main/cpu/source/data}"
  fi
  export CP2K_LOCAL_BIN="$binary"
  export CP2K_LOCAL_VARIANT="$variant"
}

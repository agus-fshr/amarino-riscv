#!/usr/bin/env bash
# Download the pinned verification tools into .tools/ (git-ignored).
# Usage: tools/setup_harness_tools.sh [all|cad|sail|formal|archtest|bench] ...
# Every artifact is pinned by version and checked against a sha256 or commit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="${HARNESS_TOOLS_DIR:-$ROOT/.tools}"
mkdir -p "$TOOLS/dl"

# ---- pins -------------------------------------------------------------------
CAD_DATE=2026-11-24
CAD_URL="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/$CAD_DATE/oss-cad-suite-linux-x64-${CAD_DATE//-/}.tgz"
CAD_SHA256=b19b923934ff1625c8d8057d1f652e37f4272bcb6a5e391df5b090d06ff51efb

SAIL_VERSION=0.16
SAIL_URL="https://github.com/riscv/sail-riscv/releases/download/$SAIL_VERSION/sail-riscv-Linux-x86_64.tar.gz"
SAIL_SHA256=f9bb8a32b8f3eddbdf69b3a29d96dff4ba1e77f45877165e0478418367da6bee

RVFORMAL_REPO=https://github.com/YosysHQ/riscv-formal.git
RVFORMAL_COMMIT=c992aa61fdfe0846c5ed90324c596202a1c69b76

ARCHTEST_REPO=https://github.com/riscv-non-isa/riscv-arch-test.git
ARCHTEST_TAG=3.9.1
ARCHTEST_COMMIT=eb66181dd27ff7847e2c3a010705b13490b0bf75   # tag 3.9.1

# Benchmark sources for gate 4 (only the files named in harness/gate4_bench are used).
COREMARK_REPO=https://github.com/eembc/coremark.git
COREMARK_COMMIT=1f483d5b8316753a742cbf5590caf5bd0a4e4777
RVTESTS_REPO=https://github.com/riscv-software-src/riscv-tests.git
RVTESTS_COMMIT=1b3f8bd665d79e5985753a558dae4e3718e2edc8   # benchmarks/dhrystone
# ------------------------------------------------------------------------------

fetch() {  # url sha256 dest
  local url=$1 sha=$2 dest=$3
  if [[ ! -f $dest ]] || ! echo "$sha  $dest" | sha256sum -c --quiet - 2>/dev/null; then
    echo "downloading $url"
    curl -fL --retry 3 -o "$dest.part" "$url"
    echo "$sha  $dest.part" | sha256sum -c --quiet -
    mv "$dest.part" "$dest"
  fi
}

stamp_ok() { [[ -f "$1/.pinned" && "$(cat "$1/.pinned")" == "$2" ]]; }

setup_cad() {
  local dir="$TOOLS/oss-cad-suite"
  stamp_ok "$dir" "$CAD_SHA256" && return
  fetch "$CAD_URL" "$CAD_SHA256" "$TOOLS/dl/oss-cad-suite.tgz"
  rm -rf "$dir"; tar -xzf "$TOOLS/dl/oss-cad-suite.tgz" -C "$TOOLS"
  echo "$CAD_SHA256" > "$dir/.pinned"
}

setup_sail() {
  local dir="$TOOLS/sail"
  stamp_ok "$dir" "$SAIL_SHA256" && return
  fetch "$SAIL_URL" "$SAIL_SHA256" "$TOOLS/dl/sail.tar.gz"
  rm -rf "$dir"; mkdir -p "$dir"; tar -xzf "$TOOLS/dl/sail.tar.gz" -C "$dir" --strip-components=1
  echo "$SAIL_SHA256" > "$dir/.pinned"
}

setup_git() {  # name repo commit [ref-to-fetch]
  local dir="$TOOLS/$1" repo=$2 commit=$3 ref=${4:-$3}
  stamp_ok "$dir" "$commit" && return
  rm -rf "$dir"; git init -q "$dir"
  git -C "$dir" fetch -q --depth 1 "$repo" "$ref"
  git -C "$dir" checkout -q FETCH_HEAD
  [[ "$(git -C "$dir" rev-parse HEAD)" == "$commit" ]] || { echo "$1: expected $commit"; exit 1; }
  echo "$commit" > "$dir/.pinned"
}

setup_bench() {
  setup_git coremark "$COREMARK_REPO" "$COREMARK_COMMIT"
  setup_git riscv-tests "$RVTESTS_REPO" "$RVTESTS_COMMIT"
}

targets=("${@:-all}")
for t in "${targets[@]}"; do
  case $t in
    all) setup_cad; setup_sail
         setup_git riscv-formal "$RVFORMAL_REPO" "$RVFORMAL_COMMIT"
         setup_git riscv-arch-test "$ARCHTEST_REPO" "$ARCHTEST_COMMIT" "refs/tags/$ARCHTEST_TAG"
         setup_bench ;;
    cad) setup_cad ;;
    sail) setup_sail ;;
    formal) setup_git riscv-formal "$RVFORMAL_REPO" "$RVFORMAL_COMMIT" ;;
    archtest) setup_git riscv-arch-test "$ARCHTEST_REPO" "$ARCHTEST_COMMIT" "refs/tags/$ARCHTEST_TAG" ;;
    bench) setup_bench ;;
    *) echo "unknown target $t"; exit 2 ;;
  esac
done
echo "tools ready in $TOOLS"

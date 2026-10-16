#!/usr/bin/env bash
# Install shared toolchains once into $LOMA_TOOLCHAINS_DIR (default
# /opt/loma-workspace/.toolchains) instead of per conversation. Idempotent and
# locked per tool, so parallel runs wait instead of installing twice.
#
#   scripts/toolchains.sh ensure <go|playwright|clickhouse|jdk|android|flutter|ios>
#   scripts/toolchains.sh list
#   scripts/toolchains.sh migrate # one-time: move legacy top-level installs here (same volume, instant)
#   scripts/toolchains.sh env     # exports to eval in older images without the ENV block
set -euo pipefail

ROOT="${LOMA_TOOLCHAINS_DIR:-${LOMA_WORKSPACE_DIR:-/opt/loma-workspace}/.toolchains}"
CACHE="${LOMA_CACHE_DIR:-${LOMA_WORKSPACE_DIR:-/opt/loma-workspace}/.cache}"
BIN="$ROOT/bin"
JDK_MAJOR="${LOMA_JDK_MAJOR:-17}"
FLUTTER_VERSION="${LOMA_FLUTTER_VERSION:-3.29.3}"
ANDROID_CMDLINE_TOOLS="${LOMA_ANDROID_CMDLINE_TOOLS:-11076708}"
ANDROID_PACKAGES="${LOMA_ANDROID_PACKAGES:-platform-tools platforms;android-34 build-tools;34.0.0}"

case "$(uname -m)" in
  x86_64|amd64) ARCH=x64 ;;
  aarch64|arm64) ARCH=aarch64 ;;
  *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$ROOT" "$BIN" "$CACHE"

with_lock() {  # with_lock <tool> <fn>; own fd per call so nested locks work
  local fd
  exec {fd}>"$ROOT/.lock-$1"
  flock "$fd"
  "$2"
  exec {fd}>&-
}

link_bin() { ln -sfn "$1" "$BIN/$(basename "$1")"; }

install_go() {
  # Baked into the image; GOTOOLCHAIN downloads newer versions into the shared GOMODCACHE.
  go version
}

install_playwright() {
  # Browsers are keyed by the playwright version that asks for them; all versions share one dir.
  export PLAYWRIGHT_BROWSERS_PATH="$ROOT/ms-playwright"
  npx --yes playwright install chromium
}

install_clickhouse() {
  [ -x "$ROOT/clickhouse/clickhouse" ] && { "$ROOT/clickhouse/clickhouse" --version; return; }
  mkdir -p "$ROOT/clickhouse"
  ( cd "$ROOT/clickhouse" && curl -fsSL https://clickhouse.com/ | sh )
  link_bin "$ROOT/clickhouse/clickhouse"
}

install_jdk() {
  [ -x "$ROOT/jdk/bin/java" ] && { "$ROOT/jdk/bin/java" -version 2>&1 | head -1; return; }
  local tmp; tmp="$(mktemp -d "$ROOT/.jdk-XXXX")"
  curl -fsSL "https://api.adoptium.net/v3/binary/latest/$JDK_MAJOR/ga/linux/$ARCH/jdk/hotspot/normal/eclipse" \
    | tar -xz -C "$tmp" --strip-components=1
  rm -rf "$ROOT/jdk" && mv "$tmp" "$ROOT/jdk"
}

install_android() {
  with_lock jdk install_jdk
  export JAVA_HOME="$ROOT/jdk" ANDROID_HOME="$ROOT/android-sdk"
  local sdkmanager="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
  if [ ! -x "$sdkmanager" ]; then
    local tmp; tmp="$(mktemp -d "$ROOT/.android-XXXX")"
    curl -fsSL -o "$tmp/tools.zip" \
      "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}_latest.zip"
    python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$tmp/tools.zip" "$tmp"
    mkdir -p "$ANDROID_HOME/cmdline-tools"
    rm -rf "$ANDROID_HOME/cmdline-tools/latest" && mv "$tmp/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
    chmod +x "$ANDROID_HOME"/cmdline-tools/latest/bin/*
    rm -rf "$tmp"
  fi
  yes | "$sdkmanager" --licenses >/dev/null || true
  # shellcheck disable=SC2086  # package list is space separated on purpose
  "$sdkmanager" $ANDROID_PACKAGES | tr '\r' '\n' | grep -v '^\[' | tail -n 3
  if [ "$ARCH" = aarch64 ]; then
    echo "warning: Google ships adb/build-tools for x86_64 Linux only; they will not run on this arm64 host." >&2
    echo "Use the mobile-e2e skill (Loma Devices) to run apps on an emulator." >&2
  fi
}

install_flutter() {
  local dir="$ROOT/flutter"
  if [ ! -x "$dir/bin/flutter" ]; then
    git clone --depth 1 --branch "$FLUTTER_VERSION" https://github.com/flutter/flutter.git "$dir.tmp"
    rm -rf "$dir" && mv "$dir.tmp" "$dir"
  fi
  export PUB_CACHE="$CACHE/pub"
  "$dir/bin/flutter" --disable-analytics >/dev/null 2>&1 || true
  "$dir/bin/flutter" --version
}

install_ios() {
  echo "iOS builds need Xcode (macOS); this Linux host cannot install it." >&2
  echo "Use the mobile-e2e skill (Loma Devices runner on an enrolled Mac) for iOS." >&2
  exit 2
}

migrate() {  # move pre-existing installs from the workspace root; never overwrites
  local ws="${LOMA_WORKSPACE_DIR:-/opt/loma-workspace}"
  if [ -d "$ws/ms-playwright" ] && [ ! -e "$ROOT/ms-playwright" ]; then
    mv "$ws/ms-playwright" "$ROOT/ms-playwright" && echo "moved ms-playwright"
  fi
  local ch
  for ch in "$ws/toolchains/ch/clickhouse" "$ws/chlocal/clickhouse"; do
    if [ -x "$ch" ] && [ ! -e "$ROOT/clickhouse/clickhouse" ]; then
      mkdir -p "$ROOT/clickhouse" && mv "$ch" "$ROOT/clickhouse/clickhouse"
      link_bin "$ROOT/clickhouse/clickhouse" && echo "moved $ch"
    fi
  done
  echo "Left for manual review: $ws/{toolchain,toolchains,go,gopath,gocache,go-toolchain,golang}"
}

print_env() {
  cat <<EOF
export LOMA_TOOLCHAINS_DIR="$ROOT"
export UV_CACHE_DIR="$CACHE/uv" UV_LINK_MODE=hardlink
export PIP_CACHE_DIR="$CACHE/pip" npm_config_cache="$CACHE/npm"
export GOPATH="$CACHE/go" GOMODCACHE="$CACHE/go/pkg/mod" GOCACHE="$CACHE/go-build"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/ms-playwright"
export JAVA_HOME="$ROOT/jdk" ANDROID_HOME="$ROOT/android-sdk" ANDROID_SDK_ROOT="$ROOT/android-sdk"
export GRADLE_USER_HOME="$CACHE/gradle" PUB_CACHE="$CACHE/pub"
export PATH="$BIN:$ROOT/jdk/bin:$ROOT/flutter/bin:$ROOT/android-sdk/cmdline-tools/latest/bin:$ROOT/android-sdk/platform-tools:\$PATH"
EOF
}

cmd="${1:-list}"
case "$cmd" in
  ensure)
    tool="${2:?usage: toolchains.sh ensure <go|playwright|clickhouse|jdk|android|flutter|ios>}"
    case "$tool" in
      go|playwright|clickhouse|jdk|android|flutter|ios) with_lock "$tool" "install_$tool" ;;
      *) echo "unknown tool: $tool" >&2; exit 1 ;;
    esac ;;
  list)
    echo "toolchains: $ROOT"
    for t in ms-playwright clickhouse jdk android-sdk flutter; do
      if [ -e "$ROOT/$t" ]; then echo "  $t: $(du -sh "$ROOT/$t" 2>/dev/null | cut -f1)"; else echo "  $t: not installed"; fi
    done ;;
  migrate) migrate ;;
  env) print_env ;;
  *) echo "usage: toolchains.sh <ensure TOOL|list|migrate|env>" >&2; exit 1 ;;
esac

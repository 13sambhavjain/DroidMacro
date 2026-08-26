#!/usr/bin/env bash
# Unattended baseline pass. Survives Groq 429s and a dead emulator.
#
# The runner checkpoints after every episode and skips completed ones, so
# restarting is always safe -- it never redoes work or corrupts a partial episode.
# Exit 75 (EX_TEMPFAIL) means "rate limited, more to do"; 0 means finished.
#
#   ./scripts/run_overnight.sh [extra runner args...]
set -uo pipefail
cd "$(dirname "$0")/.."

export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
PY=".venv/bin/python"
LOG="runs/overnight.log"
SLEEP_ON_429="${SLEEP_ON_429:-900}"   # 15 min
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
mkdir -p runs

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

restart_emulator() {
  say "hard-restarting the emulator (accessibility service can die while the"
  say "  device still reports booted, which fails every episode instantly)"
  adb emu kill >/dev/null 2>&1 || true
  pkill -f 'qemu-system-aarch64' >/dev/null 2>&1 || true
  sleep 8
  ensure_emulator
}

ensure_emulator() {
  if [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then return 0; fi
  say "emulator not up - starting it"
  "$ANDROID_HOME/emulator/emulator" -avd AndroidWorldAvd -no-snapshot -grpc 8554 \
      >> runs/emulator.log 2>&1 &
  for _ in $(seq 1 24); do
    sleep 10
    [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && { say "emulator booted"; sleep 5; return 0; }
  done
  say "ERROR: emulator failed to boot in 4 min"
  return 1
}

say "=== overnight run starting (args: $*) ==="
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  ensure_emulator || { say "sleeping ${SLEEP_ON_429}s before retrying emulator"; sleep "$SLEEP_ON_429"; continue; }

  say "--- attempt $attempt ---"
  "$PY" -m flowagent.runner --provider groq "$@" 2>&1 | tee -a "$LOG"
  code=${PIPESTATUS[0]}

  case "$code" in
    0)  say "=== COMPLETE (nothing left to run) ==="
        say "flows recorded: $(ls runs/flows/*.json 2>/dev/null | wc -l | tr -d ' ')"
        exit 0 ;;
    75) say "rate limited - sleeping ${SLEEP_ON_429}s, then resuming from the checkpoint"
        sleep "$SLEEP_ON_429" ;;
    76) say "environment failure or unfinished episodes - restarting emulator and resuming"
        restart_emulator || sleep 60 ;;
    *)  say "unexpected exit $code - sleeping 120s and retrying"
        sleep 120 ;;
  esac
done
say "=== gave up after $MAX_ATTEMPTS attempts ==="
exit 1

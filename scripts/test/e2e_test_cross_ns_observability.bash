#!/bin/bash
#
# E2E regression smoke test for cross-NS observability (Layer 1).
#
# Brings up a talker + listener in the *same* IPC namespace (no `unshare`)
# and asserts that the ros2agnocast CLI verbs still surface them correctly
# via the new procfs path. This catches the case where the procfs fall-back
# or the wrapper's `_all_ns` symbols regress the existing intra-NS scenario.
#
# Requires:
#   * agnocast kmod (the new one with `/proc/agnocast/`) loaded:
#       sudo insmod agnocast_kmod/agnocast.ko
#   * workspace built:
#       bash scripts/dev/build_all.bash
#
# Usage:
#   bash scripts/test/e2e_test_cross_ns_observability.bash
#
# Exit codes:
#   0 — all checks passed
#   1 — prerequisite failure (kmod not loaded, workspace not built)
#   2 — assertion failure (CLI output didn't match expectation)

set -u

ROOT_DIR=$(cd "$(dirname "$0")/../.." && pwd)
TOPIC_NAME=${TOPIC_NAME:-/my_topic}
TALKER_NODE=${TALKER_NODE:-/talker_node}
LISTENER_NODE=${LISTENER_NODE:-/listener_node}
TIMEOUT_MS=${TIMEOUT_MS:-200}

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# ----- prerequisites -----
if ! grep -q "^agnocast " /proc/modules; then
    red "ERROR: agnocast kmod not loaded."
    echo "  → sudo insmod $ROOT_DIR/agnocast_kmod/agnocast.ko" >&2
    exit 1
fi
for f in topics nodes topic_info; do
    if [ ! -r "/proc/agnocast/$f" ]; then
        red "ERROR: /proc/agnocast/$f not readable."
        echo "  → kmod doesn't expose procfs. Reload the new kmod with this PR's changes." >&2
        exit 1
    fi
done
green "✓ kmod + procfs ready"

if [ ! -f "$ROOT_DIR/install/setup.bash" ]; then
    red "ERROR: workspace not built ($ROOT_DIR/install/setup.bash missing)."
    echo "  → bash $ROOT_DIR/scripts/dev/build_all.bash" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$ROOT_DIR/install/setup.bash"
green "✓ workspace sourced"

# Verify procfs schema header
header=$(head -1 /proc/agnocast/topics)
if [[ "$header" != "# schema_version="* ]]; then
    red "ERROR: /proc/agnocast/topics line 1 is '$header', expected '# schema_version=...'"
    exit 2
fi
green "✓ procfs schema_version header present"

# ----- launch talker + listener (intra-NS) -----
LOG_DIR=$(mktemp -d)
cleanup() {
    pkill -P $$ 2>/dev/null || true
    sleep 1
    rm -rf "$LOG_DIR"
}
trap cleanup EXIT

yellow "Starting talker + listener (intra-NS)…"
ros2 launch agnocast_sample_application talker.launch.xml > "$LOG_DIR/talker.log" 2>&1 &
ros2 launch agnocast_sample_application listener.launch.xml > "$LOG_DIR/listener.log" 2>&1 &

# Wait for both endpoints to register with the kmod.
for _ in $(seq 1 30); do
    pub_count=$(awk -v t="$TOPIC_NAME" '!/^#/ && $2 == t { print $3 }' /proc/agnocast/topics 2>/dev/null | head -1)
    sub_count=$(awk -v t="$TOPIC_NAME" '!/^#/ && $2 == t { print $4 }' /proc/agnocast/topics 2>/dev/null | head -1)
    if [ "${pub_count:-0}" -ge 1 ] && [ "${sub_count:-0}" -ge 1 ]; then
        break
    fi
    sleep 0.5
done

if [ "${pub_count:-0}" -lt 1 ] || [ "${sub_count:-0}" -lt 1 ]; then
    red "ERROR: talker / listener didn't register within 15s."
    echo "----- /proc/agnocast/topics -----" >&2
    cat /proc/agnocast/topics >&2
    echo "----- talker log -----" >&2
    tail -20 "$LOG_DIR/talker.log" >&2
    echo "----- listener log -----" >&2
    tail -20 "$LOG_DIR/listener.log" >&2
    exit 2
fi
green "✓ procfs reports pub_count=$pub_count, sub_count=$sub_count for $TOPIC_NAME"

# ----- CLI assertions -----
fail() {
    red "ERROR: $1"
    [ -n "${2:-}" ] && { echo "----- output -----" >&2; printf '%s\n' "$2" >&2; }
    exit 2
}

topic_list=$(ros2 topic list_agnocast --timeout-ms "$TIMEOUT_MS" 2>&1) \
    || fail "ros2 topic list_agnocast exited non-zero" "$topic_list"
grep -q -- "$TOPIC_NAME" <<<"$topic_list" \
    || fail "ros2 topic list_agnocast didn't include $TOPIC_NAME" "$topic_list"
grep -- "$TOPIC_NAME" <<<"$topic_list" | grep -q "Agnocast enabled" \
    || fail "$TOPIC_NAME present but missing '(Agnocast enabled)' annotation" "$topic_list"
green "✓ ros2 topic list_agnocast"

node_list=$(ros2 node list_agnocast --timeout-ms "$TIMEOUT_MS" 2>&1) \
    || fail "ros2 node list_agnocast exited non-zero" "$node_list"
grep -q -- "$TALKER_NODE" <<<"$node_list" \
    || fail "ros2 node list_agnocast didn't include $TALKER_NODE" "$node_list"
grep -q -- "$LISTENER_NODE" <<<"$node_list" \
    || fail "ros2 node list_agnocast didn't include $LISTENER_NODE" "$node_list"
green "✓ ros2 node list_agnocast"

topic_info=$(ros2 topic info_agnocast "$TOPIC_NAME" --timeout-ms "$TIMEOUT_MS" 2>&1) \
    || fail "ros2 topic info_agnocast exited non-zero" "$topic_info"
grep -qE "Agnocast Publisher count:\s*1" <<<"$topic_info" \
    || fail "topic info_agnocast didn't report 1 Agnocast publisher" "$topic_info"
grep -qE "Agnocast Subscription count:\s*1" <<<"$topic_info" \
    || fail "topic info_agnocast didn't report 1 Agnocast subscription" "$topic_info"
green "✓ ros2 topic info_agnocast"

node_info=$(ros2 node info_agnocast "$TALKER_NODE" --timeout-ms "$TIMEOUT_MS" 2>&1) \
    || fail "ros2 node info_agnocast exited non-zero" "$node_info"
grep -q -- "$TOPIC_NAME" <<<"$node_info" \
    || fail "node info_agnocast $TALKER_NODE didn't list $TOPIC_NAME" "$node_info"
green "✓ ros2 node info_agnocast"

green ""
green "===== ALL CHECKS PASSED ====="

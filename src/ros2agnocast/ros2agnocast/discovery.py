"""CLI helper for the cross-NS / cross-ECU discovery layer.

Subscribes to /_agnocast_discovery and exposes the collected messages plus a
few small data classes the verb modules consume. Imports of the discovery
msgs package are deferred so the CLI keeps working on older deployments
where the discovery agent / msgs package isn't installed.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


DISCOVERY_TOPIC = '/_agnocast_discovery'
DEFAULT_TIMEOUT_SEC = 2.0

# Match the daemon's Liveliness lease so the subscriber is QoS-compatible
# with it. Also acts as the implicit upper bound when computing
# stale-by-timestamp.
LIVELINESS_LEASE_SEC = 30.0

# Default age (seconds) past which a daemon announcement is treated as
# stale. Override per call via include_stale.
DEFAULT_STALE_AFTER_SEC = 10.0


@dataclass(frozen=True)
class Endpoint:
    node_name: str
    pid: int
    qos_depth: int
    qos_is_transient_local: bool
    qos_is_reliable: bool
    is_bridge: bool


@dataclass
class TopicEntry:
    topic_name: str
    publishers: List[Endpoint]
    subscribers: List[Endpoint]


PROC_TOPIC_INFO = '/proc/agnocast/topic_info'


def parse_proc_topic_info():
    """Return list of dict rows from /proc/agnocast/topic_info.

    Each row: {ipc_ns_inode, topic_name, direction (pub|sub), node_name, pid,
    qos_depth, qos_is_transient_local, qos_is_reliable, is_bridge}.
    Returns [] on missing/old kmod.
    """
    rows = []
    try:
        with open(PROC_TOPIC_INFO) as f:
            for line in f:
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) != 9:
                    continue
                try:
                    rows.append({
                        'ipc_ns_inode': int(parts[0]),
                        'topic_name': parts[1],
                        'direction': parts[2],
                        'node_name': parts[3],
                        'pid': int(parts[4]),
                        'qos_depth': int(parts[5]),
                        'qos_is_transient_local': parts[6] != '0',
                        'qos_is_reliable': parts[7] != '0',
                        'is_bridge': parts[8] != '0',
                    })
                except ValueError:
                    continue
    except FileNotFoundError:
        return []
    return rows


def derive_node_topics_from_rows(rows):
    """From topic_info rows / discovery topics, group topics by node.

    Returns dict[node_name] -> {'pub': set(topics), 'sub': set(topics),
    'is_bridge': bool}. Identical name across NS folds into one entry, the
    same convention `ros2 node list` uses for plain ROS 2 nodes.
    """
    by_node = {}
    for row in rows:
        entry = by_node.setdefault(row['node_name'], {
            'pub': set(), 'sub': set(), 'is_bridge': False})
        if row['direction'] == 'pub':
            entry['pub'].add(row['topic_name'])
        elif row['direction'] == 'sub':
            entry['sub'].add(row['topic_name'])
        if row.get('is_bridge'):
            entry['is_bridge'] = True
    return by_node


def discovery_to_rows(announcements):
    """Flatten AgnocastDaemonState announcements into the same row shape
    as parse_proc_topic_info(), so callers have one merge surface.
    """
    rows = []
    for msg in announcements:
        for topic in msg.topics:
            for ep in topic.publishers:
                rows.append({
                    'ipc_ns_inode': msg.ipc_ns_inode,
                    'topic_name': topic.topic_name,
                    'direction': 'pub',
                    'node_name': ep.node_name,
                    'pid': ep.pid,
                    'qos_depth': ep.qos_depth,
                    'qos_is_transient_local': ep.qos_is_transient_local,
                    'qos_is_reliable': ep.qos_is_reliable,
                    'is_bridge': ep.is_bridge,
                })
            for ep in topic.subscribers:
                rows.append({
                    'ipc_ns_inode': msg.ipc_ns_inode,
                    'topic_name': topic.topic_name,
                    'direction': 'sub',
                    'node_name': ep.node_name,
                    'pid': ep.pid,
                    'qos_depth': ep.qos_depth,
                    'qos_is_transient_local': ep.qos_is_transient_local,
                    'qos_is_reliable': ep.qos_is_reliable,
                    'is_bridge': ep.is_bridge,
                })
    return rows


def _import_msg():
    try:
        from ros2agnocast_discovery_msgs.msg import AgnocastDaemonState  # noqa: F401
        return AgnocastDaemonState
    except ImportError:
        return None


def _qos():
    from rclpy.duration import Duration
    from rclpy.qos import (
        DurabilityPolicy,
        HistoryPolicy,
        LivelinessPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )
    return QoSProfile(
        depth=1,
        history=HistoryPolicy.KEEP_LAST,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
        liveliness=LivelinessPolicy.AUTOMATIC,
        liveliness_lease_duration=Duration(seconds=LIVELINESS_LEASE_SEC),
    )


def collect_announcements(
    node,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    include_stale: bool = False,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
) -> List:
    """Subscribe to the discovery topic and collect messages for `timeout_sec`.

    Returns [] when the discovery msgs package isn't installed or no message
    arrives in the window. Latest announcement per `(host_uuid, ipc_ns_inode)`
    wins.

    By default, announcements whose `timestamp` is more than `stale_after_sec`
    behind the local clock are dropped. Set `include_stale=True` to keep them
    (mirrors the `--include-stale` CLI flag).
    """
    msg_type = _import_msg()
    if msg_type is None:
        return []

    import rclpy

    received: Dict[Tuple[str, int], object] = {}

    def cb(msg):
        received[(msg.host_uuid, msg.ipc_ns_inode)] = msg

    sub = node.create_subscription(msg_type, DISCOVERY_TOPIC, cb, _qos())
    deadline = time.monotonic() + timeout_sec
    try:
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            rclpy.spin_once(node, timeout_sec=min(remaining, 0.1))
    finally:
        node.destroy_subscription(sub)

    msgs = list(received.values())
    if include_stale:
        return msgs

    now_sec = node.get_clock().now().nanoseconds / 1e9
    fresh = []
    for msg in msgs:
        msg_sec = msg.timestamp.sec + msg.timestamp.nanosec / 1e9
        if msg_sec == 0.0:
            # Timestamp not set (e.g. some test fakes); keep the message.
            fresh.append(msg)
            continue
        if now_sec - msg_sec <= stale_after_sec:
            fresh.append(msg)
    return fresh


def merge_topics(announcements) -> Dict[str, TopicEntry]:
    """Collapse a list of AgnocastDaemonState messages into per-topic entries.

    Cross-NS duplicates (same topic name in multiple namespaces) collapse to
    a single entry whose publisher / subscriber lists are the union — matches
    `ros2 topic list` behaviour where domain scope, not NS, is the boundary.
    """
    out: Dict[str, TopicEntry] = {}
    for msg in announcements:
        for topic in msg.topics:
            entry = out.setdefault(
                topic.topic_name, TopicEntry(topic.topic_name, [], []))
            for ep in topic.publishers:
                entry.publishers.append(Endpoint(
                    node_name=ep.node_name,
                    pid=ep.pid,
                    qos_depth=ep.qos_depth,
                    qos_is_transient_local=ep.qos_is_transient_local,
                    qos_is_reliable=ep.qos_is_reliable,
                    is_bridge=ep.is_bridge,
                ))
            for ep in topic.subscribers:
                entry.subscribers.append(Endpoint(
                    node_name=ep.node_name,
                    pid=ep.pid,
                    qos_depth=ep.qos_depth,
                    qos_is_transient_local=ep.qos_is_transient_local,
                    qos_is_reliable=ep.qos_is_reliable,
                    is_bridge=ep.is_bridge,
                ))
    return out

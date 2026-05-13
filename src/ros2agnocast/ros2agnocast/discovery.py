"""Layer 2 CLI helper: subscribe to /_agnocast_discovery and collect.

Imports of the discovery msgs are deferred so the CLI keeps working if the
discovery package isn't installed (e.g. older deployments without F3 Phase B).
See CROSS_NS_OBSERVABILITY_ja.md §5.3 / §10.5.
"""

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


DISCOVERY_TOPIC = '/_agnocast_discovery'
DEFAULT_TIMEOUT_SEC = 2.0


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


def _import_msg():
    try:
        from ros2agnocast_discovery_msgs.msg import AgnocastDaemonState  # noqa: F401
        return AgnocastDaemonState
    except ImportError:
        return None


def _qos():
    from rclpy.qos import (
        DurabilityPolicy,
        HistoryPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )
    return QoSProfile(
        depth=1,
        history=HistoryPolicy.KEEP_LAST,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
    )


def collect_announcements(node, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> List:
    """Subscribe to the discovery topic and collect messages for `timeout_sec`.

    Returns [] when the discovery msgs package isn't installed or no message
    arrives in the window. Latest announcement per `(host_uuid, ipc_ns_inode)`
    wins.
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
    return list(received.values())


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

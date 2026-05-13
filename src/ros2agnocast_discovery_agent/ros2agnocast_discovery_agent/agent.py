"""Discovery agent: read /proc/agnocast/ and announce per-NS state on DDS.

See CROSS_NS_OBSERVABILITY_ja.md §5.3 / §10. One daemon per ECU in the
F3 preview implementation; later folded into the per-IPC daemon in Epic
T4DEV-52095.
"""

import os
import socket
import uuid
from collections import defaultdict
from typing import Dict, List, Tuple

import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    LivelinessPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from ros2agnocast_discovery_msgs.msg import (
    AgnocastDaemonState,
    AgnocastEndpoint,
    AgnocastTopic,
)

SCHEMA_VERSION = 1
PROC_TOPIC_INFO = '/proc/agnocast/topic_info'
DISCOVERY_TOPIC = '/_agnocast_discovery'

POLL_INTERVAL_SEC = 1.0
HEARTBEAT_INTERVAL_SEC = 10.0  # 0.1 Hz heartbeat
LIVELINESS_LEASE_SEC = 30.0    # CROSS_NS_OBSERVABILITY_ja.md §10.3


def read_machine_id() -> str:
    for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            with open(path) as f:
                raw = f.read().strip()
            if raw:
                # Format as UUID for readability where possible.
                if len(raw) == 32:
                    return str(uuid.UUID(raw))
                return raw
        except OSError:
            continue
    return 'unknown'


def parse_topic_info() -> Dict[Tuple[int, str], dict]:
    """Group `/proc/agnocast/topic_info` rows by (ipc_ns_inode, topic_name).

    Returns mapping with values shaped `{publishers: [...], subscribers: [...]}`.
    Returns empty dict if procfs is absent.
    """
    grouped: Dict[Tuple[int, str], dict] = defaultdict(
        lambda: {'publishers': [], 'subscribers': []})
    try:
        with open(PROC_TOPIC_INFO) as f:
            for line in f:
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) != 9:
                    continue
                try:
                    inum = int(parts[0])
                    topic = parts[1]
                    direction = parts[2]
                    node = parts[3]
                    pid = int(parts[4])
                    qos_depth = int(parts[5])
                    qos_tl = parts[6] != '0'
                    qos_rel = parts[7] != '0'
                    is_bridge = parts[8] != '0'
                except ValueError:
                    continue
                endpoint = AgnocastEndpoint(
                    node_name=node,
                    pid=pid,
                    qos_depth=qos_depth,
                    qos_is_transient_local=qos_tl,
                    qos_is_reliable=qos_rel,
                    is_bridge=is_bridge,
                )
                bucket = grouped[(inum, topic)]
                if direction == 'pub':
                    bucket['publishers'].append(endpoint)
                elif direction == 'sub':
                    bucket['subscribers'].append(endpoint)
    except FileNotFoundError:
        return {}
    return grouped


def build_messages(host_uuid: str, hostname: str, now) -> List[AgnocastDaemonState]:
    grouped = parse_topic_info()
    per_ns: Dict[int, List[AgnocastTopic]] = defaultdict(list)
    for (inum, topic_name), bucket in grouped.items():
        per_ns[inum].append(AgnocastTopic(
            topic_name=topic_name,
            domain_id=0,  # F2 pending; see msg comment.
            publishers=bucket['publishers'],
            subscribers=bucket['subscribers'],
        ))
    return [
        AgnocastDaemonState(
            schema_version=SCHEMA_VERSION,
            host_uuid=host_uuid,
            host_hostname=hostname,
            timestamp=now,
            ipc_ns_inode=inum,
            topics=topics,
        )
        for inum, topics in per_ns.items()
    ]


class DiscoveryAgent(Node):
    def __init__(self):
        super().__init__('agnocast_discovery_agent')
        self._host_uuid = read_machine_id()
        self._hostname = socket.gethostname()

        qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
            liveliness=LivelinessPolicy.AUTOMATIC,
            liveliness_lease_duration=Duration(seconds=LIVELINESS_LEASE_SEC),
        )
        self._pub = self.create_publisher(AgnocastDaemonState, DISCOVERY_TOPIC, qos)
        self._last_signatures: Dict[int, tuple] = {}
        self._ticks_since_heartbeat = 0
        self._heartbeat_every = max(1, int(HEARTBEAT_INTERVAL_SEC / POLL_INTERVAL_SEC))

        self.create_timer(POLL_INTERVAL_SEC, self._tick)
        self.get_logger().info(
            f'discovery agent up: host_uuid={self._host_uuid} hostname={self._hostname}')

    @staticmethod
    def _signature(msg: AgnocastDaemonState) -> tuple:
        # Stable shape for change detection. Excludes timestamp.
        return tuple(
            (
                t.topic_name,
                t.domain_id,
                tuple((e.node_name, e.pid, e.qos_depth, e.qos_is_transient_local,
                       e.qos_is_reliable, e.is_bridge) for e in t.publishers),
                tuple((e.node_name, e.pid, e.qos_depth, e.qos_is_transient_local,
                       e.qos_is_reliable, e.is_bridge) for e in t.subscribers),
            )
            for t in msg.topics
        )

    def _tick(self):
        self._ticks_since_heartbeat += 1
        send_heartbeat = self._ticks_since_heartbeat >= self._heartbeat_every

        now = self.get_clock().now().to_msg()
        messages = build_messages(self._host_uuid, self._hostname, now)
        current_signatures: Dict[int, tuple] = {}
        for msg in messages:
            sig = self._signature(msg)
            current_signatures[msg.ipc_ns_inode] = sig
            changed = self._last_signatures.get(msg.ipc_ns_inode) != sig
            if changed or send_heartbeat:
                self._pub.publish(msg)

        # If a previously-seen NS disappeared, publish an empty snapshot so
        # subscribers can drop stale entries promptly.
        for inum in list(self._last_signatures.keys()):
            if inum not in current_signatures:
                empty = AgnocastDaemonState(
                    schema_version=SCHEMA_VERSION,
                    host_uuid=self._host_uuid,
                    host_hostname=self._hostname,
                    timestamp=now,
                    ipc_ns_inode=inum,
                    topics=[],
                )
                self._pub.publish(empty)
        self._last_signatures = current_signatures
        if send_heartbeat:
            self._ticks_since_heartbeat = 0


def main():
    rclpy.init()
    node = DiscoveryAgent()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

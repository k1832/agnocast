"""Helpers for reading /proc/agnocast/ from CLI verbs.

Lets the verb modules fold cross-IPC-namespace endpoints into their existing
merge surface without crossing the NS-scoped ioctl boundary. Procfs absence
(older kmod) is handled silently — callers get an empty list and fall back
to their NS-scoped ioctl path.
"""

PROC_TOPIC_INFO = '/proc/agnocast/topic_info'


def parse_proc_topic_info():
    """Return list of dict rows from /proc/agnocast/topic_info.

    Each row: {ipc_ns_inode, topic_name, direction (pub|sub), node_name, pid,
    qos_depth, qos_is_transient_local, qos_is_reliable, is_bridge}.
    Returns [] on a missing or older kmod.
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
    """From topic_info rows, group topics by node.

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

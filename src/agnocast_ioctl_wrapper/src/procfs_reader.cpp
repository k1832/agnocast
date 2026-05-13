// Reads /proc/agnocast/{topics,topic_info} to expose cross-NS Agnocast info
// without requiring a new ioctl. Exposes the *_all_ns wrapper API used by
// the userspace CLI.

#include "agnocast_ioctl.hpp"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <set>
#include <string>
#include <vector>

namespace
{

constexpr const char * kProcTopics = "/proc/agnocast/topics";
constexpr const char * kProcTopicInfo = "/proc/agnocast/topic_info";

// Open a /proc/agnocast/* file, skipping leading comment ("# ...") lines.
// Returns nullptr if the file is absent (older kmod without procfs entries)
// or unreadable, so callers can fall back to the NS-scoped ioctl path.
FILE * open_proc_file(const char * path)
{
  FILE * fp = fopen(path, "r");
  if (!fp) {
    if (errno != ENOENT) {
      perror(path);
    }
    return nullptr;
  }
  return fp;
}

bool is_comment_or_blank(const char * line)
{
  while (*line == ' ' || *line == '\t') ++line;
  return *line == '#' || *line == '\n' || *line == '\0';
}

}  // namespace

extern "C" {

// Return a deduplicated list of topic names across all IPC namespaces visible
// in /proc/agnocast/topics. Same dedupe contract as get_agnocast_topics() so
// CLI merge code can be reused.
char ** get_agnocast_topics_all_ns(int * topic_count)
{
  *topic_count = 0;

  FILE * fp = open_proc_file(kProcTopics);
  if (!fp) return nullptr;

  std::set<std::string> unique_topics;
  char * line = nullptr;
  size_t cap = 0;
  ssize_t n;
  while ((n = getline(&line, &cap, fp)) != -1) {
    if (is_comment_or_blank(line)) continue;

    unsigned int inum;
    char topic_name[TOPIC_NAME_BUFFER_SIZE];
    unsigned int pub_count, sub_count;
    if (
      sscanf(line, "%u %255s %u %u", &inum, topic_name, &pub_count, &sub_count) == 4) {
      unique_topics.emplace(topic_name);
    }
  }
  free(line);
  fclose(fp);

  if (unique_topics.empty()) return nullptr;

  char ** topic_array =
    static_cast<char **>(malloc(unique_topics.size() * sizeof(char *)));
  if (!topic_array) return nullptr;

  size_t i = 0;
  for (const auto & name : unique_topics) {
    topic_array[i] = static_cast<char *>(malloc(TOPIC_NAME_BUFFER_SIZE + 1));
    if (!topic_array[i]) {
      for (size_t j = 0; j < i; ++j) free(topic_array[j]);
      free(topic_array);
      return nullptr;
    }
    std::strncpy(topic_array[i], name.c_str(), TOPIC_NAME_BUFFER_SIZE);
    topic_array[i][TOPIC_NAME_BUFFER_SIZE] = '\0';
    ++i;
  }
  *topic_count = static_cast<int>(unique_topics.size());
  return topic_array;
}

// Read /proc/agnocast/topic_info, collecting rows where topic_name matches and
// direction equals want_direction ("pub" or "sub"). Returns nullptr if none.
static struct topic_info_ret * collect_topic_info(
  const char * topic_name, const char * want_direction, int * count)
{
  *count = 0;

  FILE * fp = open_proc_file(kProcTopicInfo);
  if (!fp) return nullptr;

  std::vector<struct topic_info_ret> results;
  char * line = nullptr;
  size_t cap = 0;
  ssize_t n;
  while ((n = getline(&line, &cap, fp)) != -1) {
    if (is_comment_or_blank(line)) continue;

    unsigned int inum;
    char row_topic[TOPIC_NAME_BUFFER_SIZE];
    char direction[8];
    char node_name[NODE_NAME_BUFFER_SIZE];
    int pid;
    unsigned int qos_depth;
    int qos_tl, qos_rel, is_bridge;
    int matched = sscanf(
      line, "%u %255s %7s %255s %d %u %d %d %d", &inum, row_topic, direction, node_name, &pid,
      &qos_depth, &qos_tl, &qos_rel, &is_bridge);
    if (matched != 9) continue;

    if (std::strcmp(row_topic, topic_name) != 0) continue;
    if (std::strcmp(direction, want_direction) != 0) continue;

    struct topic_info_ret row{};
    std::strncpy(row.node_name, node_name, NODE_NAME_BUFFER_SIZE - 1);
    row.qos_depth = qos_depth;
    row.qos_is_transient_local = qos_tl != 0;
    row.qos_is_reliable = qos_rel != 0;
    row.is_bridge = is_bridge != 0;
    results.push_back(row);
  }
  free(line);
  fclose(fp);

  if (results.empty()) return nullptr;

  auto * buf = static_cast<struct topic_info_ret *>(
    malloc(results.size() * sizeof(struct topic_info_ret)));
  if (!buf) return nullptr;
  std::memcpy(buf, results.data(), results.size() * sizeof(struct topic_info_ret));
  *count = static_cast<int>(results.size());
  return buf;
}

struct topic_info_ret * get_agnocast_sub_nodes_all_ns(
  const char * topic_name, int * count)
{
  return collect_topic_info(topic_name, "sub", count);
}

struct topic_info_ret * get_agnocast_pub_nodes_all_ns(
  const char * topic_name, int * count)
{
  return collect_topic_info(topic_name, "pub", count);
}

}  // extern "C"

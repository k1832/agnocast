# One Agnocast topic and its endpoints within a single IPC namespace.
string topic_name
# Reserved. The kmod currently has a single domain, so this is always 0.
# Will carry meaningful values once per-domain topic keys land.
uint32 domain_id
AgnocastEndpoint[] publishers
AgnocastEndpoint[] subscribers

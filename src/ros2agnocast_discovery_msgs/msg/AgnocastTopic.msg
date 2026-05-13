# One Agnocast topic and its endpoints within a single IPC namespace.
string topic_name
# domain_id: 0 in the F3 pre-implementation (kmod is single-domain). Carries
# meaning once Epic F2 (domain separation) lands.
uint32 domain_id
AgnocastEndpoint[] publishers
AgnocastEndpoint[] subscribers

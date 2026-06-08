from __future__ import annotations


POD_RESTART = "pod_restart"
KAFKA_REBALANCE = "kafka_rebalance"
LATENCY = "latency"
DNS_FAILURE = "dns_failure"
CASSANDRA_TIMEOUT = "cassandra_timeout"
OOM_MEMORY = "oom_memory"
GENERIC = "generic"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def classify_skill(alert: str) -> str:
    text = alert.lower()

    if _contains_any(
        text,
        (
            "crashloopbackoff",
            "crash loop",
            "pod restarted",
            "restarts",
            "restart",
        ),
    ):
        return POD_RESTART

    if _contains_any(
        text,
        (
            "oomkilled",
            "out of memory",
            "memory",
        ),
    ):
        return OOM_MEMORY

    if _contains_any(
        text,
        (
            "unknownhostexception",
            "nxdomain",
            "cluster.local",
            "dns",
        ),
    ):
        return DNS_FAILURE

    if _contains_any(
        text,
        (
            "cassandra",
            "cqlsession",
            "local_quorum",
        ),
    ) or ("read timeout" in text and "cassandra" in text):
        return CASSANDRA_TIMEOUT

    if _contains_any(
        text,
        (
            "rebalance",
            "consumer group",
            "consumer lag",
            "unknown_topic_or_partition",
            "kafka",
        ),
    ):
        return KAFKA_REBALANCE

    if _contains_any(
        text,
        (
            "latency",
            "timeout",
            "slow",
            "p95",
            "p99",
        ),
    ):
        return LATENCY

    return GENERIC


SKILL_PROMPTS = {
    POD_RESTART: """\
## Selected investigation skill: pod_restart
Treat this as a pod/container restart investigation unless evidence says otherwise.
Do not assume Kafka.
- First identify affected pods and restart counts.
- Use kubectl_get for pods, deployments, ReplicaSets, and events.
- Use kubectl_describe on affected pods and the owning deployment.
- Check lastState, termination reason, exit code, and restart timestamps.
- Check liveness, readiness, and startup probe failures.
- Check for OOMKilled, node pressure, eviction, image pull, and config/secret mount issues.
- Use query_logs for historical application errors across previous pod incarnations.
- Use kubectl_logs only for current pod state.
- Use prometheus_query for restart rate, memory, CPU, and saturation if metrics are reachable.
- Correlate with recent rollout or ReplicaSet changes.
- Only investigate Kafka if logs, events, or service context actually indicate Kafka.
""",
    KAFKA_REBALANCE: """\
## Selected investigation skill: kafka_rebalance
Treat this as a Kafka consumer group or broker metadata investigation.
- A consumer logging "group is already rebalancing" can be a bystander; keep looking for the trigger.
- Broker metadata errors such as UNKNOWN_TOPIC_OR_PARTITION across groups point to a shared cause.
- Search logs for poll timeout, max.poll, leaving group, rebalance, revoke, and LeaveGroup.
- Check Event Streams consumer groups and topics when relevant.
- Distinguish one bad pod from an external broker/topic/group issue by breaking evidence down by pod, group, and topic.
""",
    LATENCY: """\
## Selected investigation skill: latency
Treat this as a latency or timeout investigation unless evidence says otherwise.
- Check RED metrics: rate, errors, and duration.
- Identify whether all pods or one pod are affected.
- Check dependency timeout logs and downstream errors.
- Check restarts, CPU/memory saturation, thread pools, queueing, and resource pressure.
- Correlate latency changes with rollout, scaling, dependency, or traffic changes.
""",
    DNS_FAILURE: """\
## Selected investigation skill: dns_failure
Treat this as a DNS or service discovery investigation unless evidence says otherwise.
- Check service names, endpoints, namespace, and pod selectors.
- Look for UnknownHostException, NXDOMAIN, cluster.local, and ndots-related symptoms.
- Use kubectl_get for services, endpoints, and pods.
- Use query_logs to find affected services, hostnames, and timing.
- Check whether failures are isolated to one pod/node or affect multiple callers.
""",
    CASSANDRA_TIMEOUT: """\
## Selected investigation skill: cassandra_timeout
Treat this as a Cassandra timeout investigation only when the alert or evidence indicates Cassandra.
- Check Cassandra timeout logs, CqlSession errors, read/write timeout text, consistency level, and LOCAL_QUORUM.
- Identify affected service, query shape hints, partitions, and operation type when logs expose them.
- Check latency, saturation, CPU/memory, restarts, and downstream dependency errors.
- Do not assume Cassandra when the alert/logs do not indicate Cassandra.
""",
    OOM_MEMORY: """\
## Selected investigation skill: oom_memory
Treat this as a memory pressure or OOM investigation unless evidence says otherwise.
- Check OOMKilled, exit code 137, memory working set, limits, VPA/resource settings, and restart timestamps.
- Check node pressure, eviction events, and container termination state.
- Correlate memory growth with rollout, traffic, workload, or dependency changes.
- Use query_logs for historical application errors and prometheus_query for memory trends if reachable.
""",
    GENERIC: """\
## Selected investigation skill: generic
Use the base SRE investigation method. Do not assume a domain-specific cause until evidence supports it.
""",
}


def skill_prompt(skill: str) -> str:
    return SKILL_PROMPTS.get(skill, SKILL_PROMPTS[GENERIC])

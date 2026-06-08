from agent.prompts import SYSTEM_PROMPT
from agent.skills import (
    CASSANDRA_TIMEOUT,
    DNS_FAILURE,
    GENERIC,
    KAFKA_REBALANCE,
    OOM_MEMORY,
    POD_RESTART,
    classify_skill,
    skill_prompt,
)


def test_classify_pod_restart():
    assert classify_skill("event-data pods have 5 restarts") == POD_RESTART


def test_classify_kafka_rebalance():
    assert classify_skill("Kafka consumer rebalances spiking") == KAFKA_REBALANCE


def test_classify_dns_failure():
    assert classify_skill("UnknownHostException resolving service") == DNS_FAILURE


def test_classify_cassandra_timeout():
    assert classify_skill("Cassandra LOCAL_QUORUM read timeout") == CASSANDRA_TIMEOUT


def test_classify_oom_memory():
    assert classify_skill("OOMKilled in probe-data-processor") == OOM_MEMORY


def test_classify_generic_fallback():
    assert classify_skill("event-data has unusual behavior") == GENERIC


def test_base_prompt_is_not_kafka_biased():
    assert "Kafka" not in SYSTEM_PROMPT
    assert "UNKNOWN_TOPIC_OR_PARTITION" not in SYSTEM_PROMPT
    assert "rebalance" not in SYSTEM_PROMPT


def test_kafka_guidance_lives_in_kafka_skill():
    prompt = skill_prompt(KAFKA_REBALANCE)
    assert "UNKNOWN_TOPIC_OR_PARTITION" in prompt
    assert "group is already rebalancing" in prompt

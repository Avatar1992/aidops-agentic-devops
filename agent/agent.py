"""
Agentic AIOps agent (demo / educational)
- Polls Prometheus for CPU per-pod usage
- If threshold exceeded, composes a remediation plan (placeholder LLM logic)
- Sends Slack notification
- Optionally restarts deployment via k8s API or opens a GitHub PR to update Helm values (GitOps)
- Safe-by-default: DRY_RUN=True unless AUTO_APPLY=true
"""
import os
import time
import json
import requests
from kubernetes import client, config
from github import Github
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "conf", "example.env"))

PROM_URL = os.getenv("PROMETHEUS_URL")
NAMESPACE = os.getenv("NAMESPACE", "default")
DEPLOYMENT_NAME = os.getenv("DEPLOYMENT_NAME", "myapp")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
AUTO_APPLY = os.getenv("AUTO_APPLY", "false").lower() == "true"
HELM_VALUES_PATH = os.getenv("HELM_VALUES_PATH", "helm-chart/myapp/values.yaml")
IMAGE_REPO = os.getenv("IMAGE_REPOSITORY", "myapp")

# threshold (cpu cores)
CPU_THRESHOLD = float(os.getenv("CPU_THRESHOLD", "0.5"))

def post_slack(text):
    if not SLACK_WEBHOOK:
        print("Slack webhook not configured. Message:", text)
        return
    payload = {"text": text}
    try:
        resp = requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
        if resp.status_code >= 300:
            print("Slack post failed:", resp.status_code, resp.text)
    except Exception as e:
        print("Slack post exception:", e)

def query_prometheus(query):
    if not PROM_URL:
        raise RuntimeError("PROMETHEUS_URL not configured")
    url = PROM_URL.rstrip("/") + "/api/v1/query"
    r = requests.get(url, params={"query": query}, timeout=10)
    r.raise_for_status()
    return r.json()

def get_average_cpu_for_deployment(deployment_name, namespace):
    # Prometheus query: sum(rate(container_cpu_usage_seconds_total{pod=~"deployment-.*"}[1m]))
    # We’ll query by pod label app=deployment_name
    q = f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{deployment_name}.*"}}[1m]))'
    try:
        data = query_prometheus(q)
        results = data.get("data", {}).get("result", [])
        # average across pods
        values = []
        for r in results:
            v = float(r["value"][1])
            values.append(v)
        avg = sum(values) / len(values) if values else 0.0
        return avg, results
    except Exception as e:
        print("Prom query error:", e)
        return 0.0, []

def simple_llm_reasoning(issue_text):
    """
    Placeholder for an LLM / Agent call.
    Replace with real LLM integration (OpenAI/LLamaChain) in production.
    This function returns a simple remediation plan string.
    """
    # Very simple heuristic "LLM"
    plan = []
    if "high cpu" in issue_text.lower() or "cpu" in issue_text.lower():
        plan.append("Scale up replica count (increase replicas by 1 or 2)")
        plan.append("Restart pods of the deployment")
        plan.append("Check for hot loops / heavy GC in app logs")
        plan.append("If persists, create PR to bump resource limits or add HPA")
    else:
        plan.append("Investigate further, check logs and recent deployments")
    return "\n".join(plan)

def restart_deployment_k8s(deployment_name, namespace):
    # Patch deployment with an annotation to force rollout
    try:
        config.load_kube_config()
    except Exception:
        try:
            config.load_incluster_config()
        except Exception as e:
            print("Failed to load kube config:", e)
            return False, str(e)
    apps = client.AppsV1Api()
    now = datetime.utcnow().isoformat()
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "agentic-restart-at": now
                    }
                }
            }
        }
    }
    try:
        apps.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=body)
        return True, "patched"
    except Exception as e:
        return False, str(e)

def create_github_pr_update_helm(new_tag):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False, "GitHub not configured"
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    base = repo.get_branch("main")
    # create branch
    branch_name = f"agent/update-image-{new_tag.replace('.', '-')}-{int(time.time())}"
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base.commit.sha)
    except Exception as e:
        return False, f"Failed create branch: {e}"
    # get file
    try:
        contents = repo.get_contents(HELM_VALUES_PATH, ref=branch_name)
    except Exception as e:
        return False, f"Failed read helm values: {e}"
    original = contents.decoded_content.decode()
    # naive replace image tag (safe for demo)
    new_content = original.replace('tag: "latest"', f'tag: "{new_tag}"')
    if new_content == original:
        # fallback: append comment with new tag
        new_content = original + f"\n# agent update: tag {new_tag}\n"
    # update file
    try:
        repo.update_file(contents.path, f"agent: bump image tag to {new_tag}", new_content, contents.sha, branch=branch_name)
    except Exception as e:
        return False, f"Failed update file: {e}"
    # create PR
    try:
        pr = repo.create_pull(title=f"agent: bump image tag to {new_tag}", body="Automated update by Agentic AIOps", head=branch_name, base="main")
        return True, f"PR created: {pr.html_url}"
    except Exception as e:
        return False, f"Failed create PR: {e}"

def log_action(action_obj):
    print("[AGENT ACTION]", json.dumps(action_obj, indent=2))

def main_loop(poll_interval=30):
    print("Starting Agentic AIOps agent (DRY_RUN=%s, AUTO_APPLY=%s)" % (DRY_RUN, AUTO_APPLY))
    while True:
        avg_cpu, results = get_average_cpu_for_deployment(DEPLOYMENT_NAME, NAMESPACE)
        print(f"[{datetime.utcnow().isoformat()}] avg_cpu={avg_cpu} (threshold={CPU_THRESHOLD})")
        if avg_cpu > CPU_THRESHOLD:
            issue_text = f"High CPU detected for {DEPLOYMENT_NAME} in {NAMESPACE}: avg_cpu={avg_cpu}"
            plan = simple_llm_reasoning(issue_text)
            action = {
                "timestamp": datetime.utcnow().isoformat(),
                "deployment": DEPLOYMENT_NAME,
                "namespace": NAMESPACE,
                "avg_cpu": avg_cpu,
                "plan": plan,
                "prom_results": results
            }
            log_action(action)
            post_slack(f":rotating_light: *Agentic AIOps:* {issue_text}\nPlan:\n{plan}")

            if DRY_RUN:
                print("DRY_RUN enabled -> not applying changes.")
            else:
                if AUTO_APPLY:
                    # attempt k8s restart
                    ok, msg = restart_deployment_k8s(DEPLOYMENT_NAME, NAMESPACE)
                    print("k8s restart result:", ok, msg)
                    log_action({"apply_result": {"k8s_restart_ok": ok, "msg": msg}})
                else:
                    # create PR to bump image tag / helm values
                    new_tag = datetime.utcnow().strftime("agent-%Y%m%d%H%M%S")
                    ok, msg = create_github_pr_update_helm(new_tag)
                    print("create pr result:", ok, msg)
                    log_action({"apply_result": {"pr_ok": ok, "msg": msg}})
            # after a remediation plan, wait longer
            time.sleep(poll_interval * 2)
        else:
            time.sleep(poll_interval)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Agent stopped by user")


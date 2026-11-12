# kagent for Kafka and Kubernetes

This guide adds two read-only kagent agents to the Strimzi Minikube platform:

- **Kafka Ops** lists Strimzi `KafkaTopic` resources and reads Kafka pod logs in
  the `kafka` namespace. It reports Strimzi-managed topics, not arbitrary
  broker-only topic metadata or topic records.
- **Kubernetes Ops** inspects pods, namespace events, and pod logs using the
  built-in kagent Kubernetes MCP server.

The agents and their model configuration are reconciled by Argo CD from
[`platform/kagent/base`](./platform/kagent/base). The kagent controller,
Agent Substrate, and its development PostgreSQL database are installed once
with the upstream CLI because that installer creates required CRDs, identity
material, database credentials, and substrate components in a specific order.
The agents themselves remain declarative and GitOps-managed.

## Requirements and network layout

- Kubernetes 1.37 or later. This repository's Minikube bootstrap uses v1.37.0.
  Agent Substrate 1.0 requires Kubernetes' `PodCertificateRequest` and
  `ClusterTrustBundle` APIs.
- `kubectl`, Helm 3, and the kagent CLI v1.0.0. The CLI invokes Helm 3 for its
  installation; this is separate from the Helm version used for the other
  repository charts.
- An Ollama server running outside the Minikube cluster, with a model that
  supports tool/function calling. The example uses `qwen3:8b`.
- A DNS name for the Ollama server that is resolvable and reachable from the
  agent's network. Do not use `localhost` or `127.0.0.1`: those addresses refer
  to the agent container, not the workstation.

The Ollama service must accept connections from the Minikube node and the
Agent Substrate egress gateway. Bind Ollama to an address reachable on the
network (not only loopback), and restrict the host firewall to trusted cluster
traffic. Self-hosted Ollama does not require an API key and commonly uses
unencrypted HTTP; do not expose this port to the public Internet.

## Install and configure Ollama outside Minikube

Install Ollama on the machine that will host the model, then download a
tool-capable model:

```bash
ollama pull qwen3:8b
ollama list
```

By default, Ollama may listen only on loopback. Configure it to listen on the
host interface reachable from Minikube. For a one-time Linux shell session:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

If Ollama is already managed by systemd, configure `OLLAMA_HOST=0.0.0.0:11434`
in its service environment and restart the service instead of starting a
second server. On Windows, set the user environment variable from PowerShell
and restart Ollama:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
```

From a machine that can reach the Ollama host, confirm the daemon responds and
has the model:

```bash
curl http://ollama-host.example:11434/api/tags
```

Replace `ollama-host.example` below with a DNS name that resolves to an
address reachable from the Minikube node and agent egress gateway. A hostname
that works only from the host machine is not sufficient. The `EgressPolicy`
allows that exact HTTP origin for the agent actors; the Agent Substrate
gateway still needs a route to it. When Ollama is on a private LAN, verify
that the cluster's egress setup permits reaching that private destination.

## Install kagent 1.0.0

The upstream CLI install is intended for evaluation and installs a development
PostgreSQL database. For production, follow kagent's Helm installation guide
with a separately managed PostgreSQL database.

Install the kagent CLI v1.0.0 following the
[official installation instructions](https://kagent.dev/docs/kagent/latest/setup/installation/).
On Linux, the upstream installer can be used as follows:

```bash
curl -fsSL https://kagent.dev/install | bash -s -- --version v1.0.0
kagent version
helm version
```

Ensure `helm version` reports Helm 3 before installing kagent. Create a kagent
CLI configuration file. This enables gRPC reflection for debugging; no model
API key is needed for self-hosted Ollama:

```bash
cat > kagent-values.yaml <<'EOF'
config:
  server:
    grpcReflection: true
EOF

cat > kagent-config.yaml <<'EOF'
install:
  kagent:
    valuesFiles:
      - kagent-values.yaml
EOF

kagent --config kagent-config.yaml install --namespace kagent
kubectl rollout status deployment/kagent-controller -n kagent --timeout=300s
```

The CLI installs the CRDs, Agent Substrate, its certificate controller,
PostgreSQL for this development setup, the kagent controller, and the built-in
tool server. The `kagent` namespace is declared in the platform namespace
manifests, so the CLI can use that namespace if Argo CD has already created it.

## Configure the external Ollama endpoint

Before Argo CD applies the agent configuration, update the Ollama origin in
both:

- [`model-config.yaml`](./platform/kagent/base/model-config.yaml), under
  `spec.ollama.host`
- [`ollama-egress-policy.yaml`](./platform/kagent/base/ollama-egress-policy.yaml),
  under `spec.destinations[].origin`

Set both values to exactly the same reachable origin, for example
`http://ollama-host.example:11434`. Keep the model value in `model-config.yaml`
equal to the tag shown by `ollama list`. The model must support tool calling.

The ModelConfig and EgressPolicy are Kubernetes resources, so these settings
are managed in Git. Do not put Ollama Cloud keys or other credentials in these
files; a self-hosted Ollama server does not need an API key.

## Deploy and use the agents

Commit and push the changes to the branch tracked by the root Application
(`main` by default). Argo CD creates the `kagent-workloads` child Application
and syncs the ModelConfig, EgressPolicy, shared Harness, and both agents from
[`platform/kagent/base`](./platform/kagent/base). This Application uses
automatic sync and retries while the kagent CRDs or controller are not yet
available. After the CLI installation, check:

```bash
kubectl -n argocd get application kagent-workloads
kubectl get remotemcpserver -n kagent
kubectl get modelconfig,harness,agent,agenttemplate -n kagent
kubectl get agent kafka-ops kubernetes-ops -n kagent
```

The built-in server should include `k8s_get_resources`,
`k8s_get_pod_logs`, and `k8s_get_events`. Ask the Kafka Ops agent to list the
topics in `kafka`, then ask Kubernetes Ops to inspect events or logs for a
specific pod. For example, with `jq` installed:

```bash
SESSION_ID="$(kagent agent session create --agent kafka-ops -o json | jq -r '.session.id')"
kagent agent invoke --session "$SESSION_ID" --task "List Strimzi-managed Kafka topics in the kafka namespace."
```

A session uses the Agent revision it was created from; create a new session
after changing the template, model, or tools.

The selected MCP tools are read-oriented, but tool selection is not a
Kubernetes authorization boundary. Review the built-in tool server's service
account permissions and cluster access for your environment. Keep GitOps
credentials and other secrets out of the prompts and logs.

## Troubleshooting

| Symptom | Checks |
| --- | --- |
| Agent is not ready | Check `kubectl describe agent <name> -n kagent`, the referenced AgentTemplate and Harness, and the kagent controller logs. |
| MCP tools are unavailable | Check that `kagent-tool-server` is enabled and `kubectl get remotemcpserver -n kagent` reports it accepted. |
| Model requests fail | Confirm the Ollama process is listening on a reachable interface, the hostname resolves from the cluster, port 11434 is allowed through the host firewall, and the ModelConfig host/origin match the EgressPolicy. |
| Agent answers without inspecting data | Verify its new Session uses the latest Agent revision and that the required MCP tool names appear in the `RemoteMCPServer` discovered tool list. |
| Ollama responds but tools are not called | Use a tool-calling-capable model and ensure it is available under the exact configured Ollama tag. |

For version-specific requirements and upgrades, consult the
[kagent installation guide](https://kagent.dev/docs/kagent/latest/setup/installation/),
[Ollama provider guide](https://kagent.dev/docs/kagent/latest/setup/model-providers/ollama/),
and [tools ecosystem reference](https://kagent.dev/docs/kagent/latest/reference/tools-ecosystem/).

# Strimzi in this deployment

This guide explains Strimzi and how it manages Kafka in this repository. It is
for readers who are new to Kubernetes operators as well as Strimzi. The setup
described here is a learning lab on Minikube, not a production reference.

For cluster topology and GitOps details, see
[`ARCHITECTURE.md`](./ARCHITECTURE.md). To install the complete environment,
follow the deployment steps in [`README.md`](./README.md).

## What is Strimzi?

Strimzi is a Kubernetes operator for Apache Kafka. It does not replace Kafka:
it provides Kubernetes APIs and controllers that make Kafka clusters, topics,
and users manageable as Kubernetes resources.

Kubernetes normally understands built-in resource types such as Pods,
Deployments, and Services. Strimzi installs **CustomResourceDefinitions
(CRDs)**, which extend the Kubernetes API with Kafka-specific types. This
repository uses:

| Strimzi resource | Purpose here |
|---|---|
| `Kafka` | Describes the Kafka cluster version, listeners, security, and broker configuration |
| `KafkaNodePool` | Describes the number, roles, storage, and resources for Kafka nodes |
| `KafkaTopic` | Declares a Kafka topic and its partition and replication settings |
| `KafkaUser` | Declares a Kafka client identity, authentication method, and ACLs |

A custom resource (CR) is an instance of one of these types. For example,
`Kafka/local-kafka` is the desired configuration for this deployment's Kafka
cluster.

## Operator and reconciliation

The **Strimzi Cluster Operator** is the controller that watches `Kafka` and
`KafkaNodePool` resources. It compares each resource's declared desired state
with the current cluster state, then creates or updates the Kubernetes
resources needed to make them match. It also reports progress and problems
back in the custom resource's status.

The flow in this repository has two controllers with distinct jobs:

1. **Argo CD** reads the version-controlled YAML and creates or updates the
   Strimzi custom resources in Kubernetes.
2. **Strimzi** sees those custom resources and manages the Kafka cluster
   resources, including broker/controller pods, Services, storage claims,
   listener configuration, and certificates.
3. Strimzi's **Entity Operator**, which runs with the Kafka deployment, runs
   the Topic Operator and User Operator. They reconcile `KafkaTopic` and
   `KafkaUser` resources against Kafka itself.

In short, Git describes the desired resources, Argo CD applies them, and
Strimzi operates Kafka to match them. A manually edited Strimzi-managed Pod or
Service is generated state: make durable changes to the owning CR in Git
instead.

## The resources used here

### Kafka cluster

[`platform/kafka/base/kafka-cluster.yaml`](./platform/kafka/base/kafka-cluster.yaml)
declares the `Kafka` resource named `local-kafka`. It sets Kafka version
`4.3.1`, enables KRaft, configures the internal client listener on port
`9092`, enables SCRAM-SHA-512 authentication and simple ACL authorization,
and sets replication and metrics options.

KRaft is Kafka's built-in metadata management mode; this deployment has no
ZooKeeper. The Kafka resource describes cluster-wide settings, not the number
of Kafka processes or their storage. Those are in the node pool.

### Kafka node pool

[`platform/kafka/base/kafka-nodepool.yaml`](./platform/kafka/base/kafka-nodepool.yaml)
declares a `KafkaNodePool` named `local` and labels it as belonging to
`local-kafka`. It requests three replicas. Each replica has both the `broker`
and `controller` roles, with a 5 Gi persistent claim.

This combined-role, three-member pool is deliberately sized for a local lab.
All three pods run on one Minikube node and therefore do not provide
infrastructure-level high availability. Production deployments should use a
topology and storage plan designed for their failure domains and workload.

The cluster association is important: the KafkaNodePool's
`strimzi.io/cluster: local-kafka` label connects it to the Kafka resource.
The same label on a KafkaTopic or KafkaUser tells Strimzi which cluster the
entity belongs to.

### Topics and users

The base directory includes two `KafkaTopic` resources:

- [`kafka-topic.yaml`](./platform/kafka/base/kafka-topic.yaml) creates
  `demo-events`.
- [`kafka-topic-test-topic.yaml`](./platform/kafka/base/kafka-topic-test-topic.yaml)
  creates `test-topic` for the HTTP demo.

Each declares three partitions and a replication factor of three. A partition
is a unit of parallelism and ordering in a topic; replicas are copies of
partitions on different Kafka nodes. With auto topic creation disabled in the
Kafka resource, declare topics explicitly rather than relying on a client
request to create one implicitly.

[`kafka-user.yaml`](./platform/kafka/base/kafka-user.yaml) declares the
`app-user` identity used by the HTTP demo. Strimzi generates a Kubernetes
Secret named `app-user`, including the SCRAM password. The `KafkaUser` ACLs
specify the topics and consumer group this identity may access.

The topic explorer uses a separate `KafkaUser` declared in
[`kafka-user-topic-explorer.yaml`](./platform/kafka/base/kafka-user-topic-explorer.yaml).
Its read-oriented ACLs let Redpanda Console list topics and consume records.
The generated Secret is mounted into the Console Deployment as environment
variables; passwords are not stored in Git.

### Listeners and connecting clients

A Kafka listener is a network endpoint through which clients connect. The
internal listener is available to pods in the cluster at
`local-kafka-kafka-bootstrap:9092`. It uses SASL_PLAINTEXT with SCRAM-SHA-512:
clients authenticate, but traffic on this listener is not encrypted with TLS.
Use it only for the intended in-cluster learning environment.

The local overlay adds a separate TLS NodePort listener for host-side Kafka
clients. Its advertised bootstrap and broker addresses are reported in the
Kafka resource status. Kafka clients first contact a bootstrap endpoint, then
use broker addresses returned in metadata, so those advertised addresses must
also be reachable by the client.

Redpanda Console is a web UI, not a Kafka broker. It connects through the
internal listener using its own SCRAM user. An Nginx sidecar protects the UI
with Basic Auth using a hash supplied from a Kubernetes Secret. The Console
listens on the pod's loopback interface so clients cannot bypass the proxy
through its pod IP. The Service is ClusterIP-only; use `kubectl port-forward`
for local browser access.

## How to follow a change

For example, to change a topic's partition count:

1. Edit the corresponding `KafkaTopic` YAML in `platform/kafka/base`.
2. Commit and push the change to the branch tracked by Argo CD.
3. Argo CD updates the `KafkaTopic` custom resource.
4. The Strimzi Topic Operator observes the change and reconciles the topic.
5. Check the custom resource's `Ready` status and inspect the topic in Kafka
   or the Redpanda Console UI.

Do not edit Kafka's generated Deployment or broker Pod to change topic
configuration. Strimzi owns and may recreate those resources.

## Check cluster health

Use the custom resource conditions as well as Pod health. A Pod running only
means its container started; it does not prove that the Kafka cluster or a
topic has finished reconciling.

```bash
# See the Kafka-specific resources and generated workloads.
kubectl -n kafka get kafka,kafkanodepool,kafkatopic,kafkauser,pods,svc,pvc

# Wait for the cluster and an example topic to reconcile.
kubectl -n kafka wait kafka/local-kafka --for=condition=Ready --timeout=15m
kubectl -n kafka wait kafkatopic/test-topic --for=condition=Ready --timeout=5m
kubectl -n kafka wait kafkauser/app-user --for=condition=Ready --timeout=5m

# Inspect a resource's conditions and recent events.
kubectl -n kafka describe kafka local-kafka
kubectl -n kafka describe kafkatopic test-topic

# Inspect operator logs when a resource is not becoming Ready.
kubectl -n strimzi-system logs deployment/strimzi-cluster-operator --tail=100
kubectl -n kafka get pods
kubectl -n kafka logs deployment/local-kafka-entity-operator -c topic-operator --tail=100
```

If a resource is missing, first check whether its manifest is included in the
Kustomization and whether the corresponding Argo CD Application has synced.
If it exists but is not `Ready`, inspect its conditions, Events, and relevant
operator logs before changing generated resources.

## Key paths

| Path | What to look at |
|---|---|
| [`applications/base/strimzi.yaml`](./applications/base/strimzi.yaml) | Argo CD Application that installs the Strimzi operator chart |
| [`infrastructure/strimzi/values.yaml`](./infrastructure/strimzi/values.yaml) | Helm values for the operator |
| [`applications/base/kafka.yaml`](./applications/base/kafka.yaml) | Argo CD Application that deploys Kafka custom resources |
| [`platform/kafka/base/kustomization.yaml`](./platform/kafka/base/kustomization.yaml) | List of Kafka resources included in the base |
| [`platform/kafka/overlays/local/kustomization.yaml`](./platform/kafka/overlays/local/kustomization.yaml) | Local-only TLS NodePort listener patch |
| [`platform/kafka-demo/base/deployment.yaml`](./platform/kafka-demo/base/deployment.yaml) | HTTP demo's broker address and SCRAM Secret reference |
| [`platform/redpanda-console/base/config-map.yaml`](./platform/redpanda-console/base/config-map.yaml) | Console's Kafka bootstrap and SCRAM mechanism |

For upstream details and version compatibility, consult the
[Strimzi documentation](https://strimzi.io/documentation/).

# Kafka HTTP demo

This learning service exposes an HTTP endpoint that produces JSON messages to
`test-topic` and consumes messages from the same topic in a background thread.
The consumer prints received records to the container log.

The service connects to `local-kafka-kafka-bootstrap:9092` from within the
`kafka` namespace. It uses the repository's existing **plaintext** listener.
This listener does not enable SCRAM authentication, so the `KafkaUser` ACLs
are not used by this demo. The HTTP endpoint also has no client authentication
and is intended only for this local learning cluster; do not expose it or use
this configuration in production.

## Build and deploy to Minikube

Build the image into the active Minikube profile before Argo CD creates the
Deployment. The manifest uses `imagePullPolicy: Never` because this image is
local and is not published to a registry:

```bash
minikube image build -t kafka-http-demo:dev services/kafka-http-demo
kubectl kustomize platform/kafka-demo/base
```

Commit and push the source and manifests to the tracked Git branch. Argo CD
then creates the `kafka-http-demo` Application and deploys the service.

Check application and workload health:

```bash
kubectl -n argocd get application kafka-http-demo
kubectl -n kafka get deployment kafka-http-demo,pods,svc
kubectl -n kafka rollout status deployment/kafka-http-demo --timeout=5m
kubectl -n kafka logs deployment/kafka-http-demo -f
```

## Send a message

In another terminal, forward the in-cluster HTTP service and post a message:

```bash
kubectl -n kafka port-forward svc/kafka-http-demo 8000:8000
```

```bash
curl -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"message":"hello Kafka","key":"demo-1"}'
```

A successful request returns the topic, partition, and offset. The background
consumer logs records from the topic, including older records because its
consumer group initially starts at the earliest available offset. The group
offset is committed automatically as it consumes.

`GET http://localhost:8000/healthz` checks that the HTTP process is running;
it does not assert Kafka connectivity. A produce request returns HTTP 503 if
the broker cannot accept the message. Inspect consumer errors with
`kubectl -n kafka logs deployment/kafka-http-demo`.

## Production considerations

For production, publish a versioned image to a trusted registry and pin it by
immutable digest. Configure TLS and listener authentication, provision
credentials through the approved secret-management workflow, and protect the
HTTP API with authentication and network policy. Review delivery semantics,
consumer offset behavior, retries, message size limits, resource sizing, and
observability before adapting this example.

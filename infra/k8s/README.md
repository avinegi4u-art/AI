# Kubernetes manifests

Plain YAML with a Kustomize overlay per environment. No Helm chart yet: templating is worth it
when there are many similar consumers of a chart, and right now there is one platform with two
environments. Kustomize keeps the base readable as the thing that actually gets applied.

```
infra/k8s/
├── base/
│   ├── kustomization.yaml
│   ├── namespace.yaml
│   ├── configmap.yaml            Non-secret settings shared by every service
│   ├── secret.example.yaml       Shape only; real values come from a secrets manager
│   ├── migrate-job.yaml          Runs before services; services wait on it
│   ├── auth.yaml                 Deployment + Service
│   ├── user.yaml
│   ├── merchant.yaml
│   ├── catalog.yaml
│   ├── gateway.yaml              Deployment + Service + Ingress
│   └── hpa.yaml                  Horizontal autoscaling for gateway and merchant
└── overlays/
    ├── staging/
    └── production/
```

## Apply

```bash
kubectl apply -k infra/k8s/overlays/staging
kubectl apply -k infra/k8s/overlays/production
```

## Conventions that matter

**Probes reflect their purpose.** `livenessProbe` hits `/health/live`, which checks nothing but
the process. `readinessProbe` hits `/health/ready`, which checks the database and Redis. If
readiness drove liveness, a brief database blip would restart every pod at once and turn a
recoverable degradation into an outage.

**Migrations are a Job with a `pre-install` ordering, and services depend on it.** A service must
never boot against a schema it does not expect. The Job runs the same migration-runner image used
locally.

**Every container is non-root with a read-only root filesystem**, no privilege escalation, and all
capabilities dropped. The images build a `marsool` user for this.

**Resource requests are set, and limits are set only on memory.** CPU limits cause throttling
that looks like a latency regression and is hard to diagnose; requests plus HPA give the
scheduling behaviour that is actually wanted. Memory has a limit because a leak should kill a pod,
not a node.

**Secrets come from a secrets manager**, mounted via the External Secrets Operator or CSI driver.
`secret.example.yaml` documents the shape and contains no values.

**Anti-affinity spreads replicas across zones**, so a zone failure degrades rather than removes a
service.

## Not yet included, deliberately

- **PodDisruptionBudgets** — needed before the first voluntary node drain in production.
- **NetworkPolicies** — the right default is deny-all with explicit service-to-service allows;
  worth adding once the service graph stops changing weekly.
- **Service mesh** — only if mutual TLS between services becomes a compliance requirement. It is
  a large operational commitment for a benefit an ingress plus network policies mostly provide.
- **Vertical Pod Autoscaler** — the resource numbers below are estimates. Real numbers come from
  load testing in Step 2, and VPA recommendations are more useful once there is real traffic.

# Archive — K8s manifests for components NOT running in the cluster

These files were removed from `kustomization.yaml` because the corresponding
services now run on dedicated VMs (architecture constraint: AI/Camunda 100% on VM .161).

| File | Service | Runs on |
|------|---------|---------|
| `ai-service-*.yaml` | FastAPI AI service | VM .161 (192.168.148.161:8443) |
| `chromadb-*.yaml` | ChromaDB vector store | VM .161 `/data/chroma` (persistent mode) |

**Do NOT add these back to kustomization.yaml** — doing so would violate the
3-zone architecture and create duplicate AI service instances.

To restore (e.g., for a pure-K8s deployment without VMs):
1. Move files back to `infra/k8s/`
2. Add them to `kustomization.yaml`
3. Update `aiops.service.base-url` to the in-cluster Service address

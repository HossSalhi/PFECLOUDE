from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "pfe-ai-service"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    enable_upstream_queries: bool = True

    # Monitoring VM is the single Prometheus source (federates K8s + all VMs)
    loki_base_url: str = "http://192.168.148.160:3100"
    prometheus_base_url: str = "http://192.168.148.160:9090"
    loki_timeout_seconds: float = 15.0
    prometheus_timeout_seconds: float = 15.0
    monitoring_vm_ip: str = "192.168.148.160"
    ai_camunda_vm_ip: str = "192.168.148.161"
    monitoring_prometheus_port: int = 9090
    ai_service_port: int = 8000

    # Local LLM model served by Ollama. Set to "aiops-sre" after running deploy-aiops-model.sh.
    llm_model: str = "aiops-sre-q4"
    llm_fallback_model: str = "qwen2.5:3b"
    llm_second_fallback_model: str = "qwen2:0.5b"
    # Fine-tuned 0.5B specialist used by InferenceEngine as the LoRA opinion gate
    llm_specialist_model: str = "aiops-sre-ft-v2"

    default_window_minutes: int = 5
    model_path: str = "model_artifacts/model.joblib"

    # ChromaDB semantic memory for auto-learning from past incidents
    # mode: "persistent" (file-based PVC) | "http" (distributed ChromaDB server)
    chroma_mode: str = "persistent"
    chroma_persist_dir: str = "/data/chroma"
    chroma_http_host: str = "chromadb"
    chroma_http_port: int = 8000
    chroma_http_ssl: bool = False
    memory_enabled: bool = True
    log_embedding_model_name: str = "all-MiniLM-L6-v2"
    log_embedding_fallback_dim: int = 384
    log_embedding_max_lines: int = 200

    # MLOps — drift detection, model versioning, auto-retraining, A/B testing
    mlops_enabled: bool = True
    drift_history_path: str = "model_artifacts/drift_history.json"
    model_registry_file: str = "model_artifacts/model_registry.json"
    model_registry_dir: str = "model_artifacts/registry"
    retrain_trigger_incidents: int = 25
    ab_test_sample_target: int = 100
    ab_test_auto_promote: bool = True
    dataset_path: str = "datasets/aiops_training_dataset.csv"
    # Minimum resolved incidents required to trigger LLM fine-tuning
    llm_retrain_min_incidents: int = 20
    # When True, auto_trainer launches the LLM fine-tuning script automatically after XGBoost
    # retraining if the dataset has >= llm_retrain_min_incidents rows.
    # Disabled by default — fine-tuning is GPU-intensive; enable with AIOPS_LLM_AUTO_RETRAIN=true.
    llm_auto_retrain: bool = False

    # Autonomous monitor — iacamunda independently monitors the entire K8s cluster
    autonomous_monitor_enabled: bool = True
    autonomous_monitor_interval_seconds: int = 120
    autonomous_monitor_cooldown_minutes: int = 15
    # Debounce: require this many consecutive anomaly detections before creating an incident.
    # 1 = no debounce (immediate, current behavior); 2 = confirm over 2 cycles (~4 min default).
    autonomous_monitor_anomaly_streak: int = 2
    # Prometheus watchdog: consecutive failures before emitting MONITORING_DOWN incident.
    prometheus_failure_threshold: int = 3
    # Comma-separated namespaces to monitor (empty = all non-system namespaces)
    autonomous_monitor_namespaces: str = ""
    # Comma-separated container/app names to NEVER auto-monitor.
    # Infra/monitoring components (grafana, loki, prometheus, …) are excluded to avoid
    # false positives from HELIOS's own stack; backend/frontend ARE intentionally monitored
    # so HELIOS can detect incidents in its own K8s deployments.
    autonomous_monitor_exclude_apps: str = "chromadb,postgres,prometheus-incluster,prometheus,ollama,camunda,ai-service,alertmanager,node-exporter,kube-state-metrics"

    # When True the monitor loop skips analysis and does not create incidents.
    # Toggle at runtime via POST /monitor/maintenance without restarting the service.
    maintenance_mode: bool = False

    # Service-to-service API key (set AIOPS_API_KEY env var).
    # When non-empty every request must carry X-API-Key: <value>.
    api_key: str = ""
    # Comma-separated K8s worker/master IPs for NodePort access to the backend
    autonomous_monitor_backend_ips: str = "192.168.148.158,192.168.148.153,192.168.148.155"
    autonomous_monitor_username: str = "admin"
    autonomous_monitor_password: str = "admin-change-me"

    model_config = SettingsConfigDict(
        env_prefix="AIOPS_",
        case_sensitive=False,
        protected_namespaces=("settings_",),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

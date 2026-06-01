def combine_features(
    log_features: dict[str, float],
    metric_features: dict[str, float],
    log_score: float,
    metric_score: float,
    extra_features: dict[str, float] | None = None,
) -> dict[str, float]:
    combined = {**log_features, **metric_features}
    if extra_features:
        combined.update(extra_features)
    combined["log_risk_score"] = log_score
    combined["metric_risk_score"] = metric_score
    combined["overall_risk_score"] = (0.5 * log_score) + (0.5 * metric_score)
    return combined

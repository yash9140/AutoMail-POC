# Grounding-strategy comparison — 2026-09-02T23:57:43.544259

provider=anthropic model=claude-sonnet-4-6
Repeated-run case: outlook_search_live_001 x3

## Comparison table

| Strategy | Semantic Accuracy | Grounding Pass | High-Quality | Center-in-Target | Mean IoU | Median IoU | Consistency (bbox IoU) | Median Latency (ms) | Total Cost |
|---|---|---|---|---|---|---|---|---|---|
| C_component_landmark_grounding | 5/5 | 0/5 | 0/5 | 0/5 | 0.0 | 0.0 | 0.719 | 14304.9 | None |

## Component/landmark special metrics (Strategy C)
### C_component_landmark_grounding
- icon center in expected row: 0/5
- label center in expected row: 0/5
- sublabel center in expected row: 0/5
- cluster (union) center in expected row: 0/5
- cluster IoU with expected row (mean): 0.0

## Per-strategy per-case detail
### C_component_landmark_grounding
- outlook_search_live_001 run 1: schema_valid=True semantic_pass=True predicted_bbox=[216, 24, 245, 76] latency_ms=14304.940699999861 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.0 overlap%=0.0 center_in_target=False
- outlook_search_live_001 run 2: schema_valid=True semantic_pass=True predicted_bbox=[220, 29, 242, 70] latency_ms=18656.806299999516 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.0 overlap%=0.0 center_in_target=False
- outlook_search_live_001 run 3: schema_valid=True semantic_pass=True predicted_bbox=[218, 27, 242, 70] latency_ms=10301.518100000976 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.0 overlap%=0.0 center_in_target=False
- outlook_search_live_002 run 1: schema_valid=True semantic_pass=True predicted_bbox=[216, 26, 242, 70] latency_ms=16536.751699999513 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.0 overlap%=0.0 center_in_target=False
- outlook_search_live_003 run 1: schema_valid=True semantic_pass=True predicted_bbox=[216, 22, 245, 78] latency_ms=13474.924800000736 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.0 overlap%=0.0 center_in_target=False

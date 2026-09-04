# Grounding-strategy comparison — 2026-09-02T23:52:44.439280

provider=anthropic model=claude-sonnet-4-6
Repeated-run case: outlook_search_live_001 x3

## Comparison table

| Strategy | Semantic Accuracy | Grounding Pass | High-Quality | Center-in-Target | Mean IoU | Median IoU | Consistency (bbox IoU) | Median Latency (ms) | Total Cost |
|---|---|---|---|---|---|---|---|---|---|
| A_baseline_runtime_prompt | 5/5 | 0/5 | 0/5 | 0/5 | 0.043 | 0.043 | 1.0 | 5352.9 | None |
| B_landmark_relative_prompt | 5/5 | 3/5 | 0/5 | 3/5 | 0.37 | 0.553 | 0.372 | 5589.7 | None |
| C_component_landmark_grounding | n/a | n/a | n/a | n/a | None | None | None | 12048.0 | None |
| D_candidate_list | 5/5 | 0/5 | 0/5 | 0/5 | 0.044 | 0.043 | 0.952 | 11752.2 | None |

## Per-strategy per-case detail
### A_baseline_runtime_prompt
- outlook_search_live_001 run 1: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=10581.45439999862 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_001 run 2: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=5044.124300000476 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_001 run 3: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=5352.915199999188 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_002 run 1: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=5770.457200000237 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_003 run 1: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=4353.2185000003665 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False

### B_landmark_relative_prompt
- outlook_search_live_001 run 1: schema_valid=True semantic_pass=True predicted_bbox=[218, 22, 282, 340] latency_ms=5476.619400000345 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.08949508042184647 overlap%=0.18265993265993266 center_in_target=False
- outlook_search_live_001 run 2: schema_valid=True semantic_pass=True predicted_bbox=[268, 42, 335, 340] latency_ms=5663.494500000525 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.5640839352109065 overlap%=0.7935906685906686 center_in_target=True
- outlook_search_live_001 run 3: schema_valid=True semantic_pass=True predicted_bbox=[272, 40, 335, 335] latency_ms=5330.787000000782 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.5527777777777778 overlap%=0.7537878787878788 center_in_target=True
- outlook_search_live_002 run 1: schema_valid=True semantic_pass=True predicted_bbox=[268, 42, 340, 335] latency_ms=5948.28619999862 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.6024464831804281 overlap%=0.8528138528138528 center_in_target=True
- outlook_search_live_003 run 1: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 340] latency_ms=5589.740299999903 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.04307492501314203 overlap%=0.08375420875420875 center_in_target=False

### C_component_landmark_grounding
- outlook_search_live_001 run 1: schema_valid=False semantic_pass=None predicted_bbox=None latency_ms=12047.98700000174 provider_error=None
- outlook_search_live_001 run 2: schema_valid=False semantic_pass=None predicted_bbox=None latency_ms=10661.049999998795 provider_error=None
- outlook_search_live_001 run 3: schema_valid=False semantic_pass=None predicted_bbox=None latency_ms=13512.466499998482 provider_error=None
- outlook_search_live_002 run 1: schema_valid=False semantic_pass=None predicted_bbox=None latency_ms=11766.297800000757 provider_error=None
- outlook_search_live_003 run 1: schema_valid=False semantic_pass=None predicted_bbox=None latency_ms=14532.581499999651 provider_error=None

### D_candidate_list
- outlook_search_live_001 run 1: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=11081.422100000054 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_001 run 2: schema_valid=True semantic_pass=True predicted_bbox=[218, 40, 275, 335] latency_ms=11528.318699998636 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.043457914768827606 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_001 run 3: schema_valid=True semantic_pass=True predicted_bbox=[218, 22, 275, 340] latency_ms=11752.193899999838 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.045699329101356836 overlap%=0.09132996632996633 center_in_target=False
- outlook_search_live_002 run 1: schema_valid=True semantic_pass=True predicted_bbox=[217, 40, 275, 340] latency_ms=263376.3972000015 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.042679003645945034 overlap%=0.08375420875420875 center_in_target=False
- outlook_search_live_003 run 1: schema_valid=True semantic_pass=True predicted_bbox=[217, 40, 275, 335] latency_ms=11794.902899999215 provider_error=None
    grounding: expected_bbox=[268.0, 8.0, 340.0, 239.0] iou=0.04306160932331757 overlap%=0.08375420875420875 center_in_target=False

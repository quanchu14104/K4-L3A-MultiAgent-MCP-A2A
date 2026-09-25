# L3A Architecture Record

Hồ sơ kiến trúc hệ thống Multi-Agent L3A phục vụ xử lý và điều tra khiếu nại thương mại điện tử.

## 1. System overview

Luồng điều phối tuần tự và kiểm chứng nghiêm ngặt:
```text
Inputs/<case_id>.json
         │
         ▼
 ┌───────────────┐
 │  Coordinator  │ ──► emit('task_assigned')
 └───────┬───────┘
         │
         ├─────────────────────────────────────────┐
         │                                         │
         ▼                                         ▼
 ┌───────────────┐                         ┌───────────────┐
 │ Specialist:   │                         │ Specialist:   │
 │ PolicyAgent   │ ──► get_policy          │ OrderAgent    │ ──► get_order, get_order_items
 └───────┬───────┘                         └───────┬───────┘
         │                                         │
         ▼                                         ▼
 ┌───────────────┐                         ┌───────────────┐
 │ Specialist:   │                         │ Specialist:   │
 │ LogisticsAgent│ ──► get_shipment_summary│ PaymentAgent  │ ──► get_order_payments, timelines
 └───────┬───────┘     get_sellers         └───────┬───────┘
         │                                         │
         └───────────────────┬─────────────────────┘
                             ▼
                    ┌─────────────────┐
                    │  VerifierAgent  │ ◄── emit('handoff', 'policy_decided')
                    └────────┬────────┘
                             │ (Cross-field validation, schema & invariant check)
                             ▼
                    ┌─────────────────┐
                    │ Outputs / Trace │ ──► emit('verification_completed', 'case_finalized')
                    └─────────────────┘
```

## 2. Agent ownership

| Actor | Input | Trách nhiệm | Output/handoff | Quyền gọi Tool |
| --- | --- | --- | --- | --- |
| `coordinator` | `case` object từ `inputs/*.json` | Tiếp nhận case, phân tích yêu cầu khiếu nại, phân rã mục tiêu cho các specialist | Giao việc cho các specialist (`task_assigned`), chuyển giao tổng hợp cho verifier (`handoff`) | Không gọi tool MCP trực tiếp |
| `policy_specialist` | `case_id`, `policy_version` | Tra cứu điều khoản chính sách có thẩm quyền | `policy_decided` với mã quyết định và `evidence_ref` | `get_policy` |
| `order_specialist` | `case_id`, `claimed_order_id` | Xác minh trạng thái đơn hàng, đối chiếu sản phẩm và danh mục | Cung cấp dữ liệu đơn hàng và danh sách `item_ids`, `seller_ids` | `get_order`, `get_order_items` |
| `logistics_specialist` | `case_id`, `claimed_order_id` | Kiểm tra mốc thời gian giao nhận, sự kiện chậm trễ, trách nhiệm người bán vs vận chuyển | Báo cáo sự kiện vận chuyển và xác định người bán liên quan | `get_shipment_summary`, `get_sellers` |
| `payment_specialist` | `case_id`, `claimed_order_id`, `claim_topic` | Điều tra lịch sử thanh toán, phát hiện duplicate charge, kiểm tra tiến độ hoàn tiền | Dữ liệu thanh toán, đối soát chênh lệch, trạng thái refund | `get_order_payments`, `get_payment_timeline`, `get_refund_timeline` |
| `verifier` | Dữ liệu tổng hợp từ các specialist | Đối chiếu logic, kiểm tra invariant, hiệu chuẩn độ tin cậy, sinh JSON đầu ra | Kết quả điều tra hoàn chỉnh (`day09-l3a-output-v2`), `verification_completed` | Không gọi tool |

## 3. A2A protocol

- **Correlation**: Mọi message và trace event đều gắn chặt với `case_id`.
- **Envelope chuẩn**: Tuân thủ `day09-trace-event-v1` bao gồm `event_id`, `case_id`, `event_type`, `occurred_at`, `actor`, `target`, `decision_code`, `evidence_refs`.
- **Chuỗi vòng đời bắt buộc**:
  1. `case_received` (Coordinator tiếp nhận)
  2. `task_assigned` (Coordinator giao việc)
  3. `tool_result_consumed` (Mỗi specialist tiêu thụ bằng chứng MCP)
  4. `policy_decided` (Policy Specialist chốt căn cứ)
  5. `handoff` (Chuyển giao bằng chứng sang Verifier)
  6. `verification_completed` (Verifier xác nhận tính toàn vẹn)
  7. `case_finalized` (Coordinator hoàn tất ghi file)

## 4. Evidence lifecycle

- Mọi bằng chứng bắt buộc phải thu thập trực tiếp từ MCP Gateway qua `gateway.call(...)`.
- Kiểm tra tính hợp lệ của schema `day09-mcp-evidence-v1` ngay khi nhận từ Gateway.
- Ghi nhận `tool_result_consumed` ngay sau khi gọi thành công kèm mã `evidence_ref`.
- Tuyệt đối không chia sẻ `evidence_ref` giữa các case (tránh vi phạm hard gate `cross_scope_evidence_ref`).
- Map chính xác từng `evidence_ref` vào claim tương ứng trong `claim_assessments`.

## 5. Failure policy

| Failure | Retry? | Fallback | Trace event/code |
| --- | --- | --- | --- |
| MCP timeout | Retry tối đa 2 lần với exponential backoff | Ghi nhận lỗi và chuyển sang chế độ an toàn | `actor="coordinator", decision_code="timeout_fallback"` |
| Not found / Tool error | Không retry | Đánh dấu dữ liệu không tồn tại, không bịa đặt | `actor="specialist", decision_code="evidence_not_found"` |
| Source conflict | Không retry | Đưa vào danh sách `data_conflicts`, ưu tiên nguồn có thẩm quyền cao hơn | `actor="verifier", decision_code="authoritative_mcp_selected"` |
| Invalid specialist result | Không retry | Verifier từ chối kết luận, xếp loại `insufficient_evidence` | `actor="verifier", decision_code="unsupported_claim"` |

## 6. Verification invariants

Trước khi xuất file output, VerifierAgent bắt buộc kiểm tra các điều kiện bất biến (invariants):
1. **Schema compliance**: Tuân thủ 100% schema `day09-l3a-output-v2.schema.json`.
2. **Entity scope**: `order_ids`, `item_ids`, `seller_ids`, `payment_references`, `shipment_ids` phải có thật từ MCP response.
3. **Evidence ownership**: Mọi `evidence_ref` trong output phải được emit trong `traces/trace.jsonl` của chính case đó.
4. **Consistency**:
   - `case_status == 'action_required'` $\iff$ `recommended_refund_brl > 0` và có ít nhất 1 `refund_line`.
   - `case_status == 'no_action'` $\iff$ `recommended_refund_brl == 0` và `refund_lines == []`.
   - Nếu trách nhiệm thuộc về seller (`late_delivery_seller`, `unavailable_order_paid`), `responsible_parties` phải có `party_type == 'seller'` và `party_id` chính xác là `seller_id` của đơn hàng.
   - `resolution_actions` không chứa phần tử trùng lặp.
5. **Confidence bounds**: Nằm trong khoảng `[0.0, 1.0]`, hiệu chuẩn ở mức `0.95` khi có bằng chứng ground-truth.

## 7. Reproducibility

- **Runtime**: Python 3.11+, MCP SDK.
- **Dependencies**: Được ghim trong `pyproject.toml` (httpx2, mcp, jsonschema, python-dotenv).
- **Concurrency**: Điều phối tuần tự từng case đảm bảo tính tất định của trace log và không gây nghẽn phiên MCP HTTP SSE.
- **Lệnh thực thi**:
  - `day09 run`
  - `day09 validate`
  - `day09 package --output dist/submission.zip`

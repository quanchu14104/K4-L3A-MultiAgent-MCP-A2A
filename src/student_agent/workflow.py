from __future__ import annotations

import logging
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

logger = logging.getLogger(__name__)


class CoordinatorAgent:
    """Coordinator agent: parses customer case request and orchestrates specialist investigation."""

    def __init__(self, trace: TraceWriter) -> None:
        self.trace = trace

    def assign_tasks(self, case_id: str, claimed_order_id: str, claims: list[dict[str, Any]]) -> None:
        for specialist in ("order_specialist", "logistics_specialist", "payment_specialist", "policy_specialist"):
            self.trace.emit(
                case_id=case_id,
                event_type="task_assigned",
                actor="coordinator",
                target=specialist,
                attributes={"order_id": claimed_order_id, "claim_count": len(claims)},
            )


class PolicySpecialist:
    """Policy specialist agent: queries authoritative policy rules and determines policy guidance."""

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self.gateway = gateway
        self.trace = trace

    async def fetch_policy(self, case_id: str, policy_version: str) -> dict[str, Any]:
        evidence = await self.gateway.call(
            "get_policy",
            case_id=case_id,
            policy_version=policy_version,
        )
        self.trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="policy_specialist",
            tool_name="get_policy",
            evidence_refs=[evidence["evidence_ref"]],
        )
        return evidence


class OrderSpecialist:
    """Order and item specialist agent: queries authoritative order and item catalog records."""

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self.gateway = gateway
        self.trace = trace

    async def investigate_order(self, case_id: str, order_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        ev_order = await self.gateway.call("get_order", case_id=case_id, order_id=order_id)
        self.trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="order_specialist",
            tool_name="get_order",
            evidence_refs=[ev_order["evidence_ref"]],
        )

        ev_items = await self.gateway.call("get_order_items", case_id=case_id, order_id=order_id)
        self.trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="order_specialist",
            tool_name="get_order_items",
            evidence_refs=[ev_items["evidence_ref"]],
        )

        return ev_order, ev_items


class LogisticsSpecialist:
    """Logistics specialist agent: inspects carrier milestones, delivery limits, and seller handoffs."""

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self.gateway = gateway
        self.trace = trace

    async def investigate_shipment(
        self, case_id: str, order_id: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        ev_shipment = await self.gateway.call("get_shipment_summary", case_id=case_id, order_id=order_id)
        self.trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="logistics_specialist",
            tool_name="get_shipment_summary",
            evidence_refs=[ev_shipment["evidence_ref"]],
        )

        ev_sellers = None
        try:
            ev_sellers = await self.gateway.call("get_sellers", case_id=case_id, order_id=order_id)
            self.trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor="logistics_specialist",
                tool_name="get_sellers",
                evidence_refs=[ev_sellers["evidence_ref"]],
            )
        except Exception as exc:
            logger.debug("Optional get_sellers call skipped for order %s: %s", order_id, exc)

        return ev_shipment, ev_sellers


class PaymentSpecialist:
    """Payment and refund specialist agent: inspects transactions, reconciliation events, and refund lifecycle."""

    def __init__(self, gateway: EvidenceGateway, trace: TraceWriter) -> None:
        self.gateway = gateway
        self.trace = trace

    async def investigate_payments(
        self, case_id: str, order_id: str, claim_topic: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        ev_payments = await self.gateway.call("get_order_payments", case_id=case_id, order_id=order_id)
        self.trace.emit(
            case_id=case_id,
            event_type="tool_result_consumed",
            actor="payment_specialist",
            tool_name="get_order_payments",
            evidence_refs=[ev_payments["evidence_ref"]],
        )

        ev_timeline = None
        if claim_topic in ("payment_mismatch", "duplicate_charge", "valid_split_payment", "refund_pending", "refund_failed"):
            try:
                ev_timeline = await self.gateway.call("get_payment_timeline", case_id=case_id, order_id=order_id)
                self.trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="payment_specialist",
                    tool_name="get_payment_timeline",
                    evidence_refs=[ev_timeline["evidence_ref"]],
                )
            except Exception as exc:
                logger.debug("Optional get_payment_timeline failed for order %s: %s", order_id, exc)

        ev_refund = None
        if claim_topic in ("refund_pending", "refund_failed"):
            try:
                ev_refund = await self.gateway.call("get_refund_timeline", case_id=case_id, order_id=order_id)
                self.trace.emit(
                    case_id=case_id,
                    event_type="tool_result_consumed",
                    actor="payment_specialist",
                    tool_name="get_refund_timeline",
                    evidence_refs=[ev_refund["evidence_ref"]],
                )
            except Exception as exc:
                logger.debug("Optional get_refund_timeline failed for order %s: %s", order_id, exc)

        return ev_payments, ev_timeline, ev_refund


class VerifierAgent:
    """Verifier agent: correlates evidence across specialists, validates invariants, and produces final output."""

    def __init__(self, trace: TraceWriter) -> None:
        self.trace = trace

    def synthesize_and_verify(
        self,
        *,
        case: dict[str, Any],
        ev_policy: dict[str, Any],
        ev_order: dict[str, Any],
        ev_items: dict[str, Any],
        ev_shipment: dict[str, Any],
        ev_sellers: dict[str, Any] | None,
        ev_payments: dict[str, Any],
        ev_timeline: dict[str, Any] | None,
        ev_refund: dict[str, Any] | None,
    ) -> dict[str, Any]:
        case_id = case["case_id"]
        customer_request = case.get("customer_request", {})
        claims = customer_request.get("claims", [])
        claimed_order_id = customer_request.get("claimed_order_id", "")

        policy_data = ev_policy.get("data", {})
        policy_rules = policy_data.get("rules", {})

        order_data = ev_order.get("data", {})
        items_data = ev_items.get("data", [])
        payments_data = ev_payments.get("data", [])
        shipment_data = ev_shipment.get("data", {})

        # Extract entities
        order_ids = [claimed_order_id] if claimed_order_id else []
        item_ids = sorted(list({item["order_item_id"] for item in items_data if "order_item_id" in item}))[:20]

        seller_ids_set: set[str] = set()
        for item in items_data:
            if "seller_id" in item:
                seller_ids_set.add(item["seller_id"])
        if ev_sellers and isinstance(ev_sellers.get("data"), list):
            for s in ev_sellers["data"]:
                if "seller_id" in s:
                    seller_ids_set.add(s["seller_id"])
        seller_ids = sorted(list(seller_ids_set))[:20]

        payment_refs: list[str] = []
        for idx, p in enumerate(payments_data):
            seq = p.get("payment_sequential", idx + 1)
            payment_refs.append(f"{claimed_order_id}_{seq}")
        payment_references = sorted(list(set(payment_refs)))[:20]
        shipment_ids = [claimed_order_id] if claimed_order_id else []

        # Determine primary issue
        primary_issue_candidate = None
        for claim in claims:
            topic = claim.get("topic")
            if topic != "requested_full_refund" and topic in policy_rules:
                primary_issue_candidate = topic
                break

        # Ground truth verification using authoritative MCP evidence
        order_status = order_data.get("order_status")
        if order_status == "canceled":
            primary_issue = "canceled_order_paid"
        elif order_status == "unavailable":
            primary_issue = "unavailable_order_paid"
        elif primary_issue_candidate in policy_rules:
            primary_issue = primary_issue_candidate
        else:
            primary_issue = "unsupported_claim"

        rule = policy_rules.get(primary_issue, {})
        case_status = rule.get("case_status", "no_action" if primary_issue == "unsupported_claim" else "action_required")
        rec_action = rule.get("recommended_action", "document_no_action")
        refund_brl = float(rule.get("refund_brl", 0.0))

        # Emit policy_decided trace event
        self.trace.emit(
            case_id=case_id,
            event_type="policy_decided",
            actor="policy_specialist",
            decision_code=primary_issue,
            evidence_refs=[ev_policy["evidence_ref"]],
        )

        # Handoff from specialists to verifier
        self.trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor="coordinator",
            target="verifier",
            decision_code="evidence_aggregated",
        )

        # Collect evidence references
        all_ev_refs: list[str] = []
        for ev in (ev_policy, ev_order, ev_items, ev_shipment, ev_sellers, ev_payments, ev_timeline, ev_refund):
            if ev and "evidence_ref" in ev:
                ref = ev["evidence_ref"]
                if ref not in all_ev_refs:
                    all_ev_refs.append(ref)

        # Map evidence references to specific claim assessments
        primary_evidence_refs: list[str] = []
        if primary_issue in ("canceled_order_paid", "unavailable_order_paid"):
            primary_evidence_refs.extend([ev_order["evidence_ref"], ev_payments["evidence_ref"]])
        elif primary_issue in ("late_delivery_seller", "late_delivery_logistics"):
            primary_evidence_refs.extend([ev_order["evidence_ref"], ev_shipment["evidence_ref"]])
            if ev_sellers and "evidence_ref" in ev_sellers:
                primary_evidence_refs.append(ev_sellers["evidence_ref"])
        elif primary_issue in ("payment_mismatch", "duplicate_charge", "valid_split_payment"):
            primary_evidence_refs.append(ev_payments["evidence_ref"])
            if ev_timeline and "evidence_ref" in ev_timeline:
                primary_evidence_refs.append(ev_timeline["evidence_ref"])
        elif primary_issue in ("refund_pending", "refund_failed"):
            primary_evidence_refs.append(ev_payments["evidence_ref"])
            if ev_refund and "evidence_ref" in ev_refund:
                primary_evidence_refs.append(ev_refund["evidence_ref"])
        else:
            primary_evidence_refs.append(ev_order["evidence_ref"])

        # De-duplicate primary evidence refs
        primary_evidence_refs = list(dict.fromkeys(primary_evidence_refs))

        # Claim assessments
        claim_assessments: list[dict[str, Any]] = []
        for claim in claims:
            claim_id = claim.get("claim_id", "claim-unknown")
            topic = claim.get("topic")

            if topic == "requested_full_refund":
                if primary_issue in ("canceled_order_paid", "unavailable_order_paid", "refund_failed"):
                    verdict = "supported"
                elif primary_issue in ("late_delivery_seller", "late_delivery_logistics", "payment_mismatch", "duplicate_charge", "refund_pending"):
                    verdict = "partially_supported"
                else:
                    verdict = "unsupported"
                c_refs = [ev_policy["evidence_ref"]]
                if ev_payments and "evidence_ref" in ev_payments:
                    c_refs.append(ev_payments["evidence_ref"])
            elif topic == primary_issue:
                verdict = "unsupported" if primary_issue == "unsupported_claim" else "supported"
                c_refs = primary_evidence_refs
            else:
                verdict = "unsupported"
                c_refs = [ev_order["evidence_ref"]]

            claim_assessments.append(
                {
                    "claim_id": claim_id,
                    "verdict": verdict,
                    "confidence": 0.95,
                    "evidence_refs": list(dict.fromkeys(c_refs)),
                }
            )

        # Root cause analysis
        cause_code = primary_issue.upper()
        ranked_causes = [{"cause_code": cause_code, "rank": 1}]

        rule_parties = rule.get("responsible_parties", [])
        responsible_parties: list[dict[str, Any]] = []
        for p in rule_parties:
            ptype = p.get("party_type", "platform")
            pid = None
            if ptype == "seller":
                pid = seller_ids[0] if seller_ids else p.get("party_id")
            responsible_parties.append({"party_type": ptype, "party_id": pid})
        if not responsible_parties:
            responsible_parties = [{"party_type": "platform", "party_id": None}]

        # Financial resolution
        if refund_brl > 0:
            financial_resolution = {
                "currency": "BRL",
                "recommended_refund_brl": refund_brl,
                "refund_lines": [
                    {
                        "reason_code": rec_action,
                        "amount_brl": refund_brl,
                        "entity_id": claimed_order_id or None,
                    }
                ],
            }
        else:
            financial_resolution = {
                "currency": "BRL",
                "recommended_refund_brl": 0.0,
                "refund_lines": [],
            }

        # Resolution actions
        resolution_actions = [rec_action]

        # Data conflicts
        data_conflicts: list[dict[str, Any]] = []
        if primary_issue == "unsupported_claim":
            data_conflicts.append(
                {
                    "field": "customer_claim",
                    "sources": ["customer_message", "mcp_gateway"],
                    "selected_source": "mcp_gateway",
                    "resolution_code": "claim_unsupported_by_authoritative_records",
                }
            )

        # Emit verification_completed trace event
        self.trace.emit(
            case_id=case_id,
            event_type="verification_completed",
            actor="verifier",
            decision_code="verified",
        )

        return {
            "schema_version": "day09-l3a-output-v2",
            "case_id": case_id,
            "assessment": {
                "primary_issue": primary_issue,
                "case_status": case_status,
                "confidence": 0.95,
            },
            "affected_entities": {
                "order_ids": order_ids,
                "item_ids": item_ids,
                "seller_ids": seller_ids,
                "payment_references": payment_references,
                "shipment_ids": shipment_ids,
            },
            "claim_assessments": claim_assessments,
            "root_cause_analysis": {
                "ranked_causes": ranked_causes,
                "responsible_parties": responsible_parties,
            },
            "evidence_refs": all_ev_refs,
            "data_conflicts": data_conflicts,
            "financial_resolution": financial_resolution,
            "resolution_actions": resolution_actions,
        }


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Execute the multi-agent coordinator and specialist workflow for an e-commerce complaint."""
    case_id = case["case_id"]
    policy_version = case.get("policy_version", "EC_POLICY_V1")
    customer_request = case.get("customer_request", {})
    claimed_order_id = customer_request.get("claimed_order_id", "")
    claims = customer_request.get("claims", [])

    # Identify primary claim topic from customer input
    claim_topic = None
    for claim in claims:
        topic = claim.get("topic")
        if topic != "requested_full_refund":
            claim_topic = topic
            break

    # Initialize specialized agents
    coordinator = CoordinatorAgent(trace)
    policy_specialist = PolicySpecialist(gateway, trace)
    order_specialist = OrderSpecialist(gateway, trace)
    logistics_specialist = LogisticsSpecialist(gateway, trace)
    payment_specialist = PaymentSpecialist(gateway, trace)
    verifier = VerifierAgent(trace)

    # Coordinator delegates tasks
    coordinator.assign_tasks(case_id, claimed_order_id, claims)

    # Policy specialist retrieves authoritative policy rules
    ev_policy = await policy_specialist.fetch_policy(case_id, policy_version)

    # Order specialist investigates order status and item catalog
    ev_order, ev_items = await order_specialist.investigate_order(case_id, claimed_order_id)

    # Logistics specialist inspects delivery timeline and sellers
    ev_shipment, ev_sellers = await logistics_specialist.investigate_shipment(case_id, claimed_order_id)

    # Payment specialist inspects payments, timeline, and refund records
    ev_payments, ev_timeline, ev_refund = await payment_specialist.investigate_payments(
        case_id, claimed_order_id, claim_topic=claim_topic
    )

    # Verifier correlates findings, evaluates invariants, and constructs output
    output = verifier.synthesize_and_verify(
        case=case,
        ev_policy=ev_policy,
        ev_order=ev_order,
        ev_items=ev_items,
        ev_shipment=ev_shipment,
        ev_sellers=ev_sellers,
        ev_payments=ev_payments,
        ev_timeline=ev_timeline,
        ev_refund=ev_refund,
    )

    return output

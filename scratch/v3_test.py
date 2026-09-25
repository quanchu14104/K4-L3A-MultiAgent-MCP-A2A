import asyncio
import json
from pathlib import Path
from student_agent.config import Settings
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import connect_gateway
from student_agent.cases import load_case_set
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


async def run_v3_test():
    root = Path(".")
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    case_set = load_case_set(root)
    trace_path = Path("traces/v3_test_trace.jsonl")
    trace_path.unlink(missing_ok=True)
    trace = TraceWriter(trace_path, contracts)

    test_cases = [f"L3A_CASE_{i:03d}" for i in range(1, 11)]
    async with connect_gateway(
        settings.mcp_endpoint, settings.team_api_key, contracts
    ) as gw:
        for cid in test_cases:
            case = case_set.cases[cid]
            trace.emit(case_id=cid, event_type="case_received", actor="coordinator")
            output = await solve_case(case, gw, trace)
            trace.emit(case_id=cid, event_type="case_finalized", actor="coordinator")
            contracts.validate_output(output, cid)

            issue = output["assessment"]["primary_issue"]
            ev_count = len(output["evidence_refs"])
            ship_ids = output["affected_entities"]["shipment_ids"]
            conf = output["assessment"]["confidence"]
            claim_verdicts = [
                f'{c["claim_id"]}={c["verdict"]}' for c in output.get("claim_assessments", [])
            ]
            print(
                f"OK {cid}: issue={issue:<24} ev={ev_count} ship={ship_ids} "
                f"conf={conf} verdicts={claim_verdicts}"
            )

    # Validate trace
    trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(trace_lines, 1):
        ev = json.loads(line)
        contracts.validate_trace(ev, f"line {idx}")
    print(f"\nALL {len(trace_lines)} TRACE LINES VALIDATED!")


if __name__ == "__main__":
    asyncio.run(run_v3_test())

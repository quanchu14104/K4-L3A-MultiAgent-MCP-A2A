import sys
import asyncio
import json
from pathlib import Path
from student_agent.config import Settings
from student_agent.contracts import Contracts
from student_agent.mcp_gateway import connect_gateway
from student_agent.cases import load_case_set

async def main():
    root = Path('.')
    settings = Settings.load(root)
    contracts = Contracts(root / 'contracts' / 'schemas')
    case_set = load_case_set(root)
    
    # Test cases representing different claim topics
    # 001: canceled_order_paid
    # 002: duplicate_charge
    # 003: late_delivery_logistics
    # 004: late_delivery_seller
    # 005: payment_mismatch
    # 006: refund_failed
    # 007: refund_pending
    # 008: unavailable_order_paid
    # 009: unsupported_claim
    # 010: valid_split_payment
    sample_cids = [
        ('L3A_CASE_001', 'canceled_order_paid'),
        ('L3A_CASE_002', 'duplicate_charge'),
        ('L3A_CASE_003', 'late_delivery_logistics'),
        ('L3A_CASE_004', 'late_delivery_seller'),
        ('L3A_CASE_005', 'payment_mismatch'),
        ('L3A_CASE_006', 'refund_failed'),
        ('L3A_CASE_007', 'refund_pending'),
        ('L3A_CASE_008', 'unavailable_order_paid'),
        ('L3A_CASE_009', 'unsupported_claim'),
        ('L3A_CASE_010', 'valid_split_payment'),
    ]

    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gw:
        for cid, expected_topic in sample_cids[4:10]:
            case = case_set.cases[cid]
            req = case['customer_request']
            oid = req['claimed_order_id']
            print(f'=== {cid} ({expected_topic}) order={oid} ===', flush=True)
            
            ev_order = await gw.call('get_order', case_id=cid, order_id=oid)
            print(f'  Order: status={ev_order["data"].get("order_status")}', flush=True)
            
            for tool_name in ['get_order_items', 'get_shipment_summary', 'get_sellers', 'get_order_payments', 'get_payment_timeline', 'get_refund_timeline']:
                try:
                    res = await gw.call(tool_name, case_id=cid, order_id=oid)
                    data = res.get('data')
                    if isinstance(data, list):
                        print(f'  {tool_name}: list[{len(data)}]', flush=True)
                        if data and len(data) <= 2:
                            print(f'    data: {data}', flush=True)
                    elif isinstance(data, dict):
                        print(f'  {tool_name}: dict with keys {list(data.keys())}', flush=True)
                        if tool_name == 'get_shipment_summary':
                            print(f'    carrier={data.get("delivered_carrier_at")} cust={data.get("delivered_customer_at")} est={data.get("estimated_delivery_at")}', flush=True)
                            print(f'    events={data.get("events")}', flush=True)
                        elif tool_name == 'get_refund_timeline':
                            print(f'    status={data.get("refund_status")} timeline={data.get("events") or data.get("timeline")}', flush=True)
                    else:
                        print(f'  {tool_name}: {data}', flush=True)
                except Exception as e:
                    print(f'  {tool_name}: FAILED ({e})', flush=True)

if __name__ == '__main__':
    asyncio.run(main())

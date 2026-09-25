import sys
import asyncio
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

    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gw:
        # Check first 20 cases
        for cid in list(case_set.case_ids)[:20]:
            case = case_set.cases[cid]
            req = case['customer_request']
            claim_topic = [c['topic'] for c in req['claims'] if c['topic'] != 'requested_full_refund'][0]
            oid = req['claimed_order_id']
            ev_order = await gw.call('get_order', case_id=cid, order_id=oid)
            ostatus = ev_order['data'].get('order_status')
            print(f'{cid}: claim={claim_topic:<24} order_status={ostatus}', flush=True)

if __name__ == '__main__':
    asyncio.run(main())

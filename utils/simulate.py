# utils/simulate.py

import time
from web3 import Web3
from config import get_rotating_http_provider
import json

SWAP_ABI = [{
    "name": "swapExactTokensForTokens",
    "type": "function",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "amountOutMin", "type": "uint256"},
        {"name": "path", "type": "address[]"},
        {"name": "to", "type": "address"},
        {"name": "deadline", "type": "uint256"}
    ],
    "outputs": [{"name": "amounts", "type": "uint256[]"}],
    "stateMutability": "nonpayable"
}]

def simulate_tx_call(router_address, amount_in, amount_out_min, path, to_address):
    """
    Simula uno swap su un DEX router usando eth_call. Restituisce un dict con:
    {
        success: bool,
        output_token_amount: int | None,
        raw_result: str | None,
        error_type: str | None,
        error_detail: str | None,
        router: str,
        path: list,
    }
    """
    result = {
        "success": False,
        "output_token_amount": None,
        "raw_result": None,
        "error_type": None,
        "error_detail": None,
        "router": router_address,
        "path": path
    }

    for attempt in range(4):
        web3 = get_rotating_http_provider()

        try:
            contract = web3.eth.contract(address=router_address, abi=SWAP_ABI)
            deadline = int(time.time()) + 120

            tx = contract.functions.swapExactTokensForTokens(
                amount_in,
                amount_out_min,
                path,
                to_address,
                deadline
            ).build_transaction({
                # 'from', 'nonce', 'gasPrice' are omitted for eth_call
                'gas': 250000
            })

            # Remove unsafe fields for eth_call
            tx.pop('nonce', None)
            tx.pop('from', None)
            tx.pop('gasPrice', None)

            call_result = web3.eth.call(tx)
            result["success"] = True
            result["raw_result"] = call_result.hex()

            try:
                # output is bytes, decode expected format
                # [amountIn, ..., amountOut]
                decoded = contract.decode_function_output("swapExactTokensForTokens", call_result)
                if decoded and isinstance(decoded[0], list):
                    result["output_token_amount"] = decoded[0][-1]
            except Exception as decode_error:
                result["error_type"] = "decode_failed"
                result["error_detail"] = str(decode_error)

            return result

        except Exception as e:
            result["error_type"] = "eth_call_failed"
            result["error_detail"] = str(e)
            time.sleep(2 ** attempt)

    return result
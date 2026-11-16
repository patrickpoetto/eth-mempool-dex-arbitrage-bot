# utils/dex.py

from web3 import Web3
from web3.exceptions import ContractLogicError
from config import ROUTERS, get_rotating_http_provider, _HTTP_PROVIDERS
import requests
import time
import os
from dotenv import load_dotenv
load_dotenv()


COMMON_TOKENS = [
    Web3.to_checksum_address("0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2"),  # WETH
    Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eb48"),  # USDC
    Web3.to_checksum_address("0x6B175474E89094C44Da98b954EedeAC495271d0F"),  # DAI
    Web3.to_checksum_address("0xdAC17F958D2ee523a2206206994597C13D831ec7"),  # USDT
    Web3.to_checksum_address("0x514910771AF9Ca656af840dff83E8264EcF986CA"),  # LINK
    Web3.to_checksum_address("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"),  # WBTC
]

ROUTER_ABI = [{
    "name": "getAmountsOut",
    "outputs": [{"name": "", "type": "uint256[]"}],
    "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
    "stateMutability": "view",
    "type": "function"
}]

UNISWAP_V3_QUOTER = Web3.to_checksum_address("0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6")

V3_QUOTER_ABI = [{
    "inputs": [
        {"internalType": "address", "name": "tokenIn", "type": "address"},
        {"internalType": "address", "name": "tokenOut", "type": "address"},
        {"internalType": "uint24", "name": "fee", "type": "uint24"},
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
    ],
    "name": "quoteExactInputSingle",
    "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
    "stateMutability": "view",
    "type": "function"
}]

def get_expected_out(dex_name, amount_in, path):
    from config import _get_rotating_http_provider_with_url, _mark_provider_unhealthy
    for attempt in range(len(_HTTP_PROVIDERS)):
        web3, url = _get_rotating_http_provider_with_url()
        try:
            router = web3.eth.contract(address=ROUTERS[dex_name], abi=ROUTER_ABI)
            amounts = router.functions.getAmountsOut(amount_in, path).call()
            return amounts[-1]
        except ContractLogicError:
            return None
        except Exception as e:
            error_str = str(e)
            # Marca provider come unhealthy per errori critici
            if "403" in error_str or "Forbidden" in error_str:
                _mark_provider_unhealthy(url, "403")
            elif "SSL" in error_str or "TLS" in error_str:
                _mark_provider_unhealthy(url, "SSL")
            elif "429" in error_str:
                _mark_provider_unhealthy(url, "429")
    return None

def get_expected_out_univ3(token_in, token_out, amount_in, fee_tiers=[3000, 10000, 500]):
    for _ in range(len(fee_tiers) * len(_HTTP_PROVIDERS)):
        web3 = get_rotating_http_provider()
        quoter = web3.eth.contract(address=UNISWAP_V3_QUOTER, abi=V3_QUOTER_ABI)
        for fee in fee_tiers:
            try:
                out = quoter.functions.quoteExactInputSingle(
                    Web3.to_checksum_address(token_in),
                    Web3.to_checksum_address(token_out),
                    fee,
                    amount_in,
                    0
                ).call()
                if out and int(out) > 0:
                    return out
            except:
                continue
    return None

def get_expected_out_curve(token_in, token_out, amount_in_wei, retries=3):
    token_in = token_in.lower()
    token_out = token_out.lower()
    url = f"https://api.curve.fi/api/get_best_rate?from={token_in}&to={token_out}&amount={amount_in_wei}"
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=3)
            data = response.json()
            if data.get("success") and "toAmount" in data.get("data", {}):
                return int(data["data"]["toAmount"])
        except Exception as e:
            print(f"[RETRY] Curve API error (attempt {attempt+1}): {e}")
            time.sleep(0.5)
    return None

def get_expected_out_balancer(token_in, token_out, amount_in_wei, retries=3):
    url = "https://api.balancer.fi/sor/route"
    params = {
        "chainId": 1,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amount": str(amount_in_wei),
        "gasPrice": str(Web3.to_wei(30, 'gwei')),
        "maxPools": 4
    }
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=3)
            data = response.json()
            if "returnAmount" in data:
                return int(data["returnAmount"])
        except Exception as e:
            print(f"[RETRY] Balancer API error (attempt {attempt+1}): {e}")
            time.sleep(0.5)
    return None

def get_expected_out_1inch(token_in, token_out, amount_in_wei, retries=3):
    url = "https://api.1inch.dev/swap/v5.2/1/quote"
    api_key = os.getenv("ONEINCH_API_KEY")
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    params = {
        "fromTokenAddress": token_in,
        "toTokenAddress": token_out,
        "amount": str(amount_in_wei)
    }
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=3)
            data = response.json()
            if "toTokenAmount" in data:
                return int(data["toTokenAmount"])
        except Exception as e:
            print(f"[RETRY] 1inch API error (attempt {attempt+1}): {e}")
        time.sleep(1.1)  # ← limite Dev Plan: 1 RPS
    return None

def get_expected_out_kyber(token_in, token_out, amount_in_wei, retries=3):
    url = "https://aggregator-api.kyberswap.com/ethereum/api/v1/routes"
    params = {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "amountIn": str(amount_in_wei),
        "saveGas": "true",
        "gasInclude": "true"
    }
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=3)
            data = response.json()
            best = data.get("data", {}).get("bestRoute")
            if best and "amountOut" in best:
                return int(best["amountOut"])
        except Exception as e:
            print(f"[RETRY] Kyber API error (attempt {attempt+1}): {e}")
            time.sleep(0.5)
    return None

def get_expected_out_paraswap(token_in, token_out, amount_in_wei, retries=3):
    url = "https://apiv5.paraswap.io/prices"
    params = {
        "srcToken": token_in,
        "destToken": token_out,
        "amount": str(amount_in_wei),
        "side": "SELL",
        "network": 1
    }
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=3)
            data = response.json()
            if "priceRoute" in data and "destAmount" in data["priceRoute"]:
                return int(data["priceRoute"]["destAmount"])
        except Exception as e:
            print(f"[RETRY] Paraswap API error (attempt {attempt+1}): {e}")
            time.sleep(0.5)
    return None

def safe_call(func, dex_name, max_retries=2):
    for attempt in range(max_retries):
        try:
            out = func()
            if out == 0:
                print(f"→ {dex_name:10}: 0 (pool vuota o token ignoto)")
                return None
            return out
        except Exception as e:
            error_str = str(e)
            # Skip silenzioso per errori comuni - evitano spam log
            if "403" not in error_str and "SSL" not in error_str and "TLS" not in error_str and "429" not in error_str:
                # Log solo per errori non comuni all'ultimo tentativo
                if attempt == max_retries - 1:
                    pass  # Silenzioso per evitare spam
    return None

def normalize_to_18_decimals(amount_raw, decimals):
    if decimals < 18:
        return amount_raw * (10 ** (18 - decimals))
    elif decimals > 18:
        return amount_raw // (10 ** (decimals - 18))
    return amount_raw

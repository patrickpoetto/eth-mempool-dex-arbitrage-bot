import os
import random
import time
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# Alchemy
ALCHEMY_HTTP = os.getenv("HTTP_ALCHEMY")
ALCHEMY_WSS  = os.getenv("WSS_ALCHEMY")

# QuickNode
QUICKNODE_HTTP = os.getenv("HTTP_QUICKNODE")
QUICKNODE_WSS  = os.getenv("WSS_QUICKNODE")

# Infura
INFURA_HTTP = os.getenv("HTTP_INFURA")
INFURA_WSS  = os.getenv("WSS_INFURA")

# Blast
BLAST_HTTP = os.getenv("HTTP_BLAST")
BLAST_WSS  = os.getenv("WSS_BLAST")

# NodeReal
NODEREAL_HTTP = os.getenv("HTTP_NODEREAL")
NODEREAL_WSS  = os.getenv("WSS_NODEREAL")

# Lista aggregata (solo quelli definiti)
_HTTP_PROVIDERS = [x for x in [ALCHEMY_HTTP, QUICKNODE_HTTP, BLAST_HTTP, NODEREAL_HTTP] if x] #INFURA_HTTP
_WSS_PROVIDERS  = [x for x in [ALCHEMY_WSS, QUICKNODE_WSS, INFURA_WSS, BLAST_WSS, NODEREAL_WSS] if x]

_http_index = 0
_wss_index = 0

# Provider health tracking - esclude temporaneamente provider problematici
_provider_health = {}  # {url: {"failures": 0, "last_failure": 0, "cooldown_until": 0}}
PROVIDER_MAX_FAILURES = 3  # Dopo 3 errori consecutivi, escludi per 5 minuti
PROVIDER_COOLDOWN_SECONDS = 300  # 5 minuti di cooldown

def _mark_provider_unhealthy(url, error_type):
    """Segna un provider come unhealthy dopo errori ripetuti - funzione pubblica per uso esterno"""
    if url not in _provider_health:
        _provider_health[url] = {"failures": 0, "last_failure": 0, "cooldown_until": 0}
    
    now = time.time()
    health = _provider_health[url]
    
    # Se il provider è in cooldown, non incrementare failures
    if now < health["cooldown_until"]:
        return
    
    # Reset failures se l'ultimo errore è stato più di 1 minuto fa (provider si è ripreso)
    if now - health["last_failure"] > 60:
        health["failures"] = 0
    
    health["failures"] += 1
    health["last_failure"] = now
    
    # Se troppi errori, metti in cooldown
    if health["failures"] >= PROVIDER_MAX_FAILURES:
        health["cooldown_until"] = now + PROVIDER_COOLDOWN_SECONDS
        provider_name = url.split("//")[1].split(".")[0] if "//" in url else url[:20]
        print(f"[⚠️] Provider {provider_name} temporaneamente escluso (errori: {error_type}) - riprova tra {PROVIDER_COOLDOWN_SECONDS//60} minuti")

def _is_provider_healthy(url):
    """Verifica se un provider è healthy (non in cooldown)"""
    if url not in _provider_health:
        return True
    
    now = time.time()
    health = _provider_health[url]
    
    # Se è in cooldown, non è healthy
    if now < health["cooldown_until"]:
        return False
    
    # Se il cooldown è scaduto, reset failures
    if health["cooldown_until"] > 0 and now >= health["cooldown_until"]:
        health["failures"] = 0
        health["cooldown_until"] = 0
        provider_name = url.split("//")[1].split(".")[0] if "//" in url else url[:20]
        print(f"[✅] Provider {provider_name} riabilitato")
        return True
    
    return True

def _get_healthy_providers(provider_list):
    """Filtra provider list per includere solo quelli healthy"""
    healthy = [p for p in provider_list if _is_provider_healthy(p)]
    # Se non ci sono provider healthy, usa tutti (meglio provare che niente)
    return healthy if healthy else provider_list

# config.py
WSS_PROVIDERS = [x for x in [
    os.getenv("WSS_ALCHEMY"),
    os.getenv("WSS_QUICKNODE"),
    os.getenv("WSS_INFURA"),
    os.getenv("WSS_BLAST"),
    os.getenv("WSS_NODEREAL")
] if x]

last_provider_url = None

def _get_rotating_http_provider_with_url():
    """Versione interna che restituisce (web3, url) per tracking"""
    global _http_index
    if not _HTTP_PROVIDERS:
        raise ValueError("❌ Nessun provider HTTP configurato! Aggiungi almeno HTTP_ALCHEMY o altro provider nel file .env")
    
    # Usa solo provider healthy
    healthy_providers = _get_healthy_providers(_HTTP_PROVIDERS)
    if not healthy_providers:
        # Se tutti sono in cooldown, usa tutti comunque (meglio che niente)
        healthy_providers = _HTTP_PROVIDERS
    
    url = healthy_providers[_http_index % len(healthy_providers)]
    _http_index = (_http_index + 1) % len(healthy_providers)
    global last_provider_url
    if url != last_provider_url:
        #print(f"[HTTP] Using provider: {url}")
        last_provider_url = url
    return Web3(Web3.HTTPProvider(url)), url

def get_rotating_http_provider():
    """Restituisce solo web3, gestisce automaticamente gli errori"""
    web3, url = _get_rotating_http_provider_with_url()
    return web3

def get_rotating_wss_provider():
    global _wss_index
    if not _WSS_PROVIDERS:
        raise ValueError("❌ Nessun provider WebSocket configurato! Aggiungi almeno WSS_ALCHEMY o altro provider nel file .env")
    
    # Usa solo provider healthy
    healthy_providers = _get_healthy_providers(_WSS_PROVIDERS)
    if not healthy_providers:
        healthy_providers = _WSS_PROVIDERS
    
    url = healthy_providers[_wss_index % len(healthy_providers)]
    _wss_index = (_wss_index + 1) % len(healthy_providers)
    return Web3(Web3.WebsocketProvider(url))

def get_rotating_wss_url():
    global _wss_index
    if not _WSS_PROVIDERS:
        raise ValueError("❌ Nessun provider WebSocket configurato! Aggiungi almeno WSS_ALCHEMY o altro provider nel file .env")
    
    # Usa solo provider healthy
    healthy_providers = _get_healthy_providers(_WSS_PROVIDERS)
    if not healthy_providers:
        healthy_providers = _WSS_PROVIDERS
    
    url = healthy_providers[_wss_index % len(healthy_providers)]
    _wss_index = (_wss_index + 1) % len(healthy_providers)
    return url  # <--- stringa URL, es. "wss://..."

# Default providers for startup (Alchemy first)
# Inizializzati solo se i provider sono disponibili
web3_http = None
web3_ws = None

if _HTTP_PROVIDERS:
    try:
        web3_http = get_rotating_http_provider()
    except Exception as e:
        print(f"[WARN] Errore inizializzazione HTTP provider: {e}")

if _WSS_PROVIDERS:
    try:
        web3_ws = get_rotating_wss_provider()
    except Exception as e:
        print(f"[WARN] Errore inizializzazione WSS provider: {e}")

# Arbitrage wallet
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if PRIVATE_KEY:
    try:
        ARBITRAGE_ADDRESS = Web3.to_checksum_address(web3_http.eth.account.from_key(PRIVATE_KEY).address) if web3_http else None
    except Exception as e:
        print(f"[WARN] Errore creazione ARBITRAGE_ADDRESS: {e}")
        ARBITRAGE_ADDRESS = None
else:
    ARBITRAGE_ADDRESS = None
    print("[WARN] PRIVATE_KEY non configurata nel .env - la simulazione delle transazioni potrebbe non funzionare")

# Chainlink ETH/USD
CHAINLINK_ETH_USD_FEED = Web3.to_checksum_address("0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419")
CHAINLINK_ABI = [{
    "inputs": [],
    "name": "latestAnswer",
    "outputs": [{"internalType": "int256", "name": "", "type": "int256"}],
    "stateMutability": "view",
    "type": "function"
}]

_last_eth_price = None
_last_eth_time = 0

def get_eth_usd_price(web3):
    global _last_eth_price, _last_eth_time
    now = time.time()

    # usa la cache se non è troppo vecchia
    if _last_eth_price is not None and (now - _last_eth_time < 30):
        return _last_eth_price

    attempts = len(_HTTP_PROVIDERS)
    for i in range(attempts):
        web3, url = _get_rotating_http_provider_with_url()
        try:
            feed = web3.eth.contract(address=CHAINLINK_ETH_USD_FEED, abi=CHAINLINK_ABI)
            price = feed.functions.latestAnswer().call()
            eth_price = float(price) / 1e8
            _last_eth_price = eth_price
            _last_eth_time = now
            return eth_price
        except Exception as e:
            error_str = str(e)
            # Marca provider come unhealthy per errori critici
            if "403" in error_str or "Forbidden" in error_str:
                _mark_provider_unhealthy(url, "403")
            elif "SSL" in error_str or "TLS" in error_str:
                _mark_provider_unhealthy(url, "SSL")
            elif "429" in error_str:
                _mark_provider_unhealthy(url, "429")
            
            # Skip silenzioso per errori comuni - evitano spam log
            delay = 2 ** i + random.uniform(0, 0.5)
            time.sleep(delay)

    # Se tutti i provider falliscono, usa cache o fallback
    if _last_eth_price is not None:
        return _last_eth_price
    return 3700.0

# Router map
ROUTERS = {
    "UniswapV2": Web3.to_checksum_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"),
    "Sushiswap": Web3.to_checksum_address("0xd9e1CE17f2641F24AE83637ab66a2cca9C378B9F"),
    "UniswapV3": Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564"),
    "Curve":     Web3.to_checksum_address("0x8e764bE4288B842791989DB5b8ec067279829809"),
    "Balancer":  Web3.to_checksum_address("0xBA12222222228d8Ba445958a75a0704d566BF2C8"),
}

# UniswapV2 info
UNISWAP_V2_ROUTER = ROUTERS["UniswapV2"]
WETH_ADDRESS = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")  # WETH on mainnet

# ABI minimale per i router (solo getAmountsOut usato per il prezzo USD)
UNISWAP_ROUTER_ABI = [{
    "name": "getAmountsOut",
    "type": "function",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path", "type": "address[]"}
    ],
    "outputs": [{"name": "amounts", "type": "uint256[]"}],
    "stateMutability": "view"
}]
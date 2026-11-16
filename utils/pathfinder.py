from itertools import permutations
import asyncio
from datetime import datetime
import json
import os
from config import get_rotating_http_provider
import random

TOKEN_MAP_PATH = os.path.join(os.path.dirname(__file__), "../utils/token_map.json")

def load_intermediate_tokens(exclude=None, max_tokens=10):
    """Carica token intermedi dal file JSON, escludendo quelli nel path attuale."""
    exclude = set(exclude or [])
    with open(TOKEN_MAP_PATH, "r") as f:
        token_map = json.load(f)

    tokens = [addr for addr in token_map if addr not in exclude]
    return tokens[:max_tokens]

def generate_paths(token_in, token_out):
    paths = []

    # Token esclusi dai middle hops
    excluded = {token_in, token_out}

    # Carica dinamicamente gli intermediari
    intermediate_tokens = load_intermediate_tokens(exclude=excluded)

    # Path diretto (1 hop)
    paths.append([token_in, token_out])

    # 2 hops
    for mid in intermediate_tokens:
        paths.append([token_in, mid, token_out])

    # 3 hops
    for i in range(len(intermediate_tokens)):
        for j in range(len(intermediate_tokens)):
            if i != j:
                mid1, mid2 = intermediate_tokens[i], intermediate_tokens[j]
                paths.append([token_in, mid1, mid2, token_out])

    return paths

FAILED_TX_LOG = []

async def fetch_transaction_with_retry(tx_hash, retries=3, delay=1.0):
    """
    Fetch transazione con retry intelligente e gestione errori migliorata.
    Riduce spam di log per errori comuni (403, SSL) e marca provider problematici.
    """
    from config import _get_rotating_http_provider_with_url, _mark_provider_unhealthy
    
    for attempt in range(1, retries + 1):
        web3, url = _get_rotating_http_provider_with_url()
        try:
            tx = await asyncio.to_thread(web3.eth.get_transaction, tx_hash)
            return tx
        except Exception as e:
            error_str = str(e)
            
            # Marca provider come unhealthy per errori critici
            if "403" in error_str or "Forbidden" in error_str:
                _mark_provider_unhealthy(url, "403")
                return None  # Skip dopo primo 403
            
            elif "SSL" in error_str or "TLS" in error_str:
                _mark_provider_unhealthy(url, "SSL")
                if attempt <= 2:
                    await asyncio.sleep(delay * attempt)
                    continue
                return None  # Skip dopo 2 tentativi SSL
            
            elif "429" in error_str:
                _mark_provider_unhealthy(url, "429")
                delay_time = min(delay * (2 ** attempt) + random.uniform(0.1, 0.5), 10.0)
                await asyncio.sleep(delay_time)
                
            elif "not found" in error_str.lower():
                # Transazione non ancora propagata - breve attesa
                await asyncio.sleep(0.5)
                if attempt >= retries:
                    return None
                    
            else:
                # Altri errori - breve attesa
                await asyncio.sleep(delay * attempt)

    # Fallimento silenzioso per evitare spam
    FAILED_TX_LOG.append(tx_hash)
    return None
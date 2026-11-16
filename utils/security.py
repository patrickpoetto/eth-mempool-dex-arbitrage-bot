from web3 import Web3
from web3.exceptions import BadFunctionCallOutput

ERC20_ABI = [
    {
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function"
    }
]

def is_token_contract_safe(web3: Web3, token_address: str) -> bool:
    """
    Verifica se il token è un contratto valido e risponde alle chiamate ERC20 di base.
    """
    try:
        token_address = Web3.to_checksum_address(token_address)

        # Verifica che il contratto esista on-chain
        code = web3.eth.get_code(token_address)
        if code in (b'', b'0x'):
            return False  # Nessun bytecode = non è un contratto

        # Verifica che risponda a symbol() e decimals()
        contract = web3.eth.contract(address=token_address, abi=ERC20_ABI)
        symbol = contract.functions.symbol().call()
        decimals = contract.functions.decimals().call()

        # Ulteriore check opzionale: simbolo almeno 2 caratteri
        if not symbol or len(symbol) < 2:
            return False

        return True

    except BadFunctionCallOutput:
        return False
    except Exception:
        return False
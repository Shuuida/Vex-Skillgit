import os
from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader

API_KEY_NAME = "X-Vex-API-Key"
api_key_header_scheme = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header_scheme)):
    """
    Verify if the request contains the correct security token.
    It is automatically disabled if VEX_REQUIRE_AUTH is not 'true'.
    """
    require_auth = os.environ.get("VEX_REQUIRE_AUTH", "False").lower() == "true"
    
    # If authentication is turned off (local development mode), we let everything through.
    if not require_auth:
        return True
        
    expected_key = os.environ.get("VEX_API_KEY")
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Server misconfiguration: VEX_API_KEY is not set in the environment."
        )
        
    if api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Forbidden: Invalid or missing API Key"
        )
    return api_key
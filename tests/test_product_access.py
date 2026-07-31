"""Contract tests for the external product-access adapter."""
import pytest
from tools import product_access


@pytest.mark.asyncio
async def test_access_adapter_fails_closed_when_unconfigured(monkeypatch):
    for name in ("PRODUCT_ACCESS_API_URL", "PRODUCT_ACCESS_API_SECRET", "PRODUCT_ACCESS_SERVICE_MEMBER_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(product_access.ProductAccessError, match="not configured"):
        await product_access.grant("product-1", "user@example.com", "User", "viewer")


def test_access_error_preserves_upstream_status():
    error = product_access.ProductAccessError("missing", 404)
    assert error.status == 404

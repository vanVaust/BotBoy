"""Keep request identities isolated even in direct adapter unit tests."""
import pytest

from botboy.gateway.app_context import _current_principal, _current_org, _current_roles


@pytest.fixture(autouse=True)
def isolated_gateway_identity():
    tokens = (_current_principal.set(""), _current_org.set("default"), _current_roles.set(()))
    try:
        yield
    finally:
        _current_principal.reset(tokens[0])
        _current_org.reset(tokens[1])
        _current_roles.reset(tokens[2])

# tests/test_identity.py
from magic_agent.identity import Erc8004Identity


class FakeRegistrar:
    def __init__(self):
        self.registered = []

    def register(self, agent_uri, metadata):
        self.registered.append((agent_uri, metadata))
        return 42  # agentId


def test_register_returns_agent_id_and_caches():
    reg = FakeRegistrar()
    ident = Erc8004Identity(reg, agent_uri="ipfs://profile", metadata={"name": "scanner-agent"})
    assert ident.agent_id is None
    aid = ident.register()
    assert aid == 42 and ident.agent_id == 42
    assert reg.registered == [("ipfs://profile", {"name": "scanner-agent"})]


def test_register_is_idempotent():
    reg = FakeRegistrar()
    ident = Erc8004Identity(reg, agent_uri="ipfs://p", metadata={})
    ident.register()
    ident.register()
    assert len(reg.registered) == 1  # second call is a no-op

from magic_agent.registration import CompetitionRegistrar, Erc8004Registrar


class _Twak:
    def __init__(self) -> None:
        self.calls = []

    def json(self, args):
        self.calls.append(args)
        return {"success": True}


class _SdkAgent:
    def __init__(self) -> None:
        self.calls = []

    def register_agent(self, *, agent_uri):
        self.calls.append((agent_uri,))
        return {"agentId": 7, "transactionHash": "0xabc"}


def test_competition_registration_uses_twak_only():
    twak = _Twak()
    CompetitionRegistrar(twak).register()
    assert twak.calls[-1][:3] == ["compete", "register", "--json"]


def test_erc8004_uses_sdk_only():
    sdk_agent = _SdkAgent()
    result = Erc8004Registrar(sdk_agent).register("ipfs://agent")
    assert result.agent_id == 7
    assert sdk_agent.calls == [("ipfs://agent",)]

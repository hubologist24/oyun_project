from tools.map_bot import run_one, BotConfig

def test_bot_is_deterministic():
    a, b = run_one(1234).to_dict(), run_one(1234).to_dict()
    assert a["issues"] == b["issues"] and a["metrics"] == b["metrics"]

def test_no_softlocks():
    bad = [s for s in range(500) if any(i.code == "SOFTLOCK" for i in run_one(s).issues)]
    assert not bad, f"softlocked seeds: {bad[:10]}"
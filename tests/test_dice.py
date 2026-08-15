from battle.core import dice


def test_seed_deterministic():
    dice.seed(42)
    a = dice.roll_dice("2d6+3")
    dice.seed(42)
    b = dice.roll_dice("2d6+3")
    assert a == b


def test_roll_dice_total():
    total, rolls = dice.roll_dice("3d4")
    assert len(rolls) == 3 and all(1 <= r <= 4 for r in rolls)
    assert total == sum(rolls)


def test_roll_d20_plain():
    dice.seed(1)
    r = dice.roll_d20(mod=5)
    assert 1 <= r["d20"] <= 20 and r["total"] == r["d20"] + 5


def test_roll_d20_advantage_uses_higher():
    dice.seed(1)
    adv = dice.roll_d20(advantage="advantage")
    assert len(adv["rolls"]) == 2 and adv["d20"] == max(adv["rolls"])
    dice.seed(1)
    dis = dice.roll_d20(advantage="disadvantage")
    assert len(dis["rolls"]) == 2 and dis["d20"] == min(dis["rolls"])


def test_roll_d20_injection():
    r = dice.roll_d20(mod=5, injected=20)
    assert r["d20"] == 20 and r["crit"] and not r["fumble"]
    r2 = dice.roll_d20(injected=1)
    assert r2["fumble"]

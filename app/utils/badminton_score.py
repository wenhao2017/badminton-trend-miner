from datetime import date

FORMAT_CHANGE = date(2027, 1, 4)


def target_for_event(start_date: date) -> int:
    return 15 if start_date >= FORMAT_CHANGE else 21


def game_won(winner: int, loser: int, target: int) -> bool:
    cap = {15: 21, 21: 30}[target]
    return winner > loser and ((winner == cap and loser >= cap - 2) or (target <= winner < cap and winner - loser >= 2))


def other_side(side: str) -> str:
    return "away" if side == "home" else "home"


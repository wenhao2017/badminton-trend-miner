def safe_division(numerator: float, denominator: float, default: float = 0.0) -> float:
    if not denominator or denominator == 0:
        return default
    return float(numerator) / float(denominator)

def calculate_rate(successes: int, total: int) -> float:
    return safe_division(successes, total, 0.0)

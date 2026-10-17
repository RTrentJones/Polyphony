"""The Wilson 95% interval for k successes in n draws (D3, Could 3)."""


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if not n:
        return None
    p, d = k / n, 1 + z * z / n
    mid, half = p + z * z / (2 * n), z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5
    return round((mid - half) / d, 2), round((mid + half) / d, 2)

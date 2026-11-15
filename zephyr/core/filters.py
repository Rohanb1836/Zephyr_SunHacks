"""zephyr/core/filters.py    One Euro filter. Owner: Aadharsh.

Tuning: if the still pointer shakes, lower min_cutoff. If the moving pointer lags, raise beta.
Reset the filter when the pointer reappears.
"""
import math


class OneEuro:
    """One Euro filter for one value. Use two (x and y) for a point."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.02, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t_s: float) -> float:
        if self.x_prev is None:
            self.x_prev, self.t_prev = x, t_s
            self.dx_prev = 0.0
            return x
        dt = max(t_s - self.t_prev, 1e-3)
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat, dx_hat, t_s
        return x_hat

    def reset(self) -> None:
        self.x_prev = None


class OneEuro2D:
    """Two One Euro filters for an (x, y) point. Parameters can change live (config reload)."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.02, d_cutoff: float = 1.0):
        self.fx = OneEuro(min_cutoff, beta, d_cutoff)
        self.fy = OneEuro(min_cutoff, beta, d_cutoff)

    def tune(self, min_cutoff: float, beta: float) -> None:
        for f in (self.fx, self.fy):
            f.min_cutoff, f.beta = float(min_cutoff), float(beta)

    def __call__(self, x: float, y: float, t_s: float) -> tuple[float, float]:
        return self.fx(x, t_s), self.fy(y, t_s)

    def reset(self) -> None:
        self.fx.reset()
        self.fy.reset()

class AsymmetricEMA:
    """Exponential moving average with separate rise and fall alphas.

    Fast attack, slow decay — loud spikes are picked up immediately
    but the smoothed value lingers rather than dropping instantly.
    """

    def __init__(self, rise_alpha: float = 0.5, fall_alpha: float = 0.05):
        self.rise_alpha = rise_alpha
        self.fall_alpha = fall_alpha
        self.value: float | None = None

    def update(self, sample: float) -> float:
        if self.value is None:
            self.value = sample
            return self.value
        alpha = self.rise_alpha if sample > self.value else self.fall_alpha
        self.value = alpha * sample + (1 - alpha) * self.value
        return self.value

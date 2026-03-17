# Smoothing Values

For your noise monitoring use case, the classic approach is an **asymmetric EMA (Exponential Moving Average)** — it rises fast, decays slow. This is perfect for audio because loud events matter immediately but you don't want the smoothed value to drop the instant noise stops.

```python
class AsymmetricEMA:
    def __init__(self, rise_alpha=0.5, fall_alpha=0.05):
        self.rise_alpha = rise_alpha  # fast attack
        self.fall_alpha = fall_alpha  # slow decay
        self.value = None

    def update(self, sample):
        if self.value is None:
            self.value = sample
            return self.value
        
        alpha = self.rise_alpha if sample > self.value else self.fall_alpha
        self.value = alpha * sample + (1 - alpha) * self.value
        return self.value
```

With your data, `rise_alpha=0.5` means a loud spike gets picked up in 1–2 samples, while `fall_alpha=0.05` means it takes ~20 samples to decay back to baseline. Tune these to taste.

---

**Why not a plain rolling average?** It would average the 79.8 dB spike down significantly — you'd see maybe 56 dB peak instead of ~74 dB in the smoothed signal, potentially missing your threshold trigger.

**Why not a plain EMA?** Symmetric rise/fall means it both reacts to and forgets peaks at the same rate. You either get a twitchy signal or a sluggish one.

---

For **peak detection on top of the smoothed value**, a simple approach is to track when the raw sample significantly exceeds your smoothed baseline:

```python
PEAK_THRESHOLD_DB = 10  # dB above smoothed baseline = event

ema = AsymmetricEMA(rise_alpha=0.5, fall_alpha=0.05)

def process_sample(db):
    smoothed = ema.update(db)
    baseline = ema.value  # before this update — or keep a slower EMA for baseline
    
    is_peak = db > (smoothed + PEAK_THRESHOLD_DB)
    return smoothed, is_peak
```

A cleaner approach for baseline vs peak is **two EMAs at different speeds** — one fast (your display smoothing) and one very slow (the ambient baseline), and flag when fast >> slow:

```python
fast_ema = AsymmetricEMA(rise_alpha=0.5, fall_alpha=0.05)   # for display
slow_ema = AsymmetricEMA(rise_alpha=0.1, fall_alpha=0.02)   # ambient baseline

def process(db):
    fast = fast_ema.update(db)
    slow = slow_ema.update(db)
    peak_event = (fast - slow) > 10  # 10 dB above ambient = noise event
    return fast, slow, peak_event
```

With your spike data (79.8 → 74.4 → 69.8 dB), the fast EMA would climb to ~73 dB while the slow baseline stays around 53–54 dB, giving you a clean 19 dB delta to trigger on — hard to miss, impossible to false-positive on the normal ±2 dB variation in your baseline data.
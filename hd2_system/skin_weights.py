"""Final half4 encoding with an exact decoded sum; no new bone influences."""
import math
import struct


def _half(value):
    return struct.unpack('<e', struct.pack('<e', value))[0]


def normalize_half4(weights, bone_keys):
    """Normalize retained influences, not the discarded authoring groups.

    Stable bone identities, rather than per-mesh palette order, break ties.
    Return already representable halves so subsequent serialization cannot
    undo the unit sum. Native passthrough streams must not call this helper.
    """
    if len(weights) != 4 or len(bone_keys) != 4:
        raise ValueError('Expected four weights and bone identities')
    values = [float(v) for v in weights]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Nonfinite or negative skin weight')
    total = math.fsum(values)
    if not math.isfinite(total) or total <= 0:
        raise ValueError('Zero/invalid total skin weight')
    reference = [v / total for v in values]
    active = [i for i, v in enumerate(values) if v > 0]
    quantized = [_half(v) for v in reference]
    if math.fsum(quantized) != 1:
        candidates = []
        for i in active:
            residual = 1 - math.fsum(quantized[j] for j in range(4) if j != i)
            if not 0 <= residual <= 1 or _half(residual) != residual:
                continue
            result = quantized.copy()
            result[i] = residual
            tie = tuple(v for _, v in sorted((str(bone_keys[k]), result[k]) for k in active))
            candidates.append((math.fsum((a-b)**2 for a,b in zip(result, reference)), tie, result))
        if candidates:
            quantized = min(candidates, key=lambda row: row[:2])[2]
        else:
            integers = [math.floor(v * 2048) for v in reference]
            remaining = 2048 - sum(integers)
            order = sorted(active, key=lambda i: (-(reference[i]*2048-integers[i]), str(bone_keys[i])))
            if not 0 <= remaining <= len(order):
                raise ValueError('Invalid weight quantization remainder')
            for i in order[:remaining]:
                integers[i] += 1
            quantized = [v / 2048 for v in integers]
    if math.fsum(quantized) != 1 or any(v < 0 or _half(v) != v for v in quantized):
        raise ValueError('Non-exact half normalization')
    if any(values[i] == 0 and quantized[i] != 0 for i in range(4)):
        raise ValueError('Unexpected new bone influence')
    if max(abs(a-b) for a,b in zip(quantized, reference)) > 1/1024:
        raise ValueError('Skin weight quantization exceeds conservative bound')
    return quantized

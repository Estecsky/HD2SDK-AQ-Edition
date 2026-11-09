"""HD2 packed NORMAL tangent angle and sign used by GF2 outline exports.

The low 20 bits are the existing octahedral normal. Z is the L1 diamond
coordinate of the tangent around that decoded normal. W=0 marks legacy or
unusable author frames, W=1 is positive native hand, and W=3 is negative
native hand. Native hand is opposite Blender UV0 hand because SDK UV export
flips V. The outline converts back to author hand for GF2_COLOR0 RGB.
"""
import math

LOW20 = (1 << 20) - 1


def _dot(a,b):
    return sum(x*y for x,y in zip(a,b))


def _cross(a,b):
    x,y,z = a
    u,v,w = b
    return y*w-z*v,z*u-x*w,x*v-y*u


def _unit(v):
    size2 = _dot(v,v)
    if not math.isfinite(size2) or size2 <= 1e-20:
        return None
    inverse = 1.0/math.sqrt(size2)
    return tuple(x*inverse for x in v)


def _oct_lattice(word):
    x = 2*(word & 1023)-1023
    y = 2*((word >> 10) & 1023)-1023
    z = 1023-abs(x)-abs(y)
    if z < 0:
        x += z if x >= 0 else -z
        y += z if y >= 0 else -z
    return x,y,z


def decode_oct_normal_word(word):
    return _unit(_oct_lattice(word))


def _frame(normal_word):
    # Select the reference axis before normalization, so quantized ties
    # resolve identically on the CPU and GPU.
    x,y,z = _oct_lattice(normal_word)
    n = _unit((x,y,z))
    t0 = _unit((y,-x,0) if abs(z) < abs(y) else (z,0,-x))
    return n,t0,_cross(t0,n)


def pack_uv0_tangent(normal_word,tangent_xyz,bitangent_sign):
    """Replace the high bits with the authored UV0 frame, retaining the normal."""
    normal_word = int(normal_word) & LOW20
    if not math.isfinite(bitangent_sign) or abs(bitangent_sign) < .9:
        return normal_word
    tangent_xyz = tuple(tangent_xyz)
    n,t0,b0 = _frame(normal_word)
    x,y = _dot(tangent_xyz,t0),_dot(tangent_xyz,b0)
    scale = abs(x)+abs(y)
    if not math.isfinite(scale) or scale <= 1e-6:
        return normal_word
    x /= scale
    angle = ((x+1.0) if y<0.0 else (3.0-x))*0.25
    angle_bits = min(1023,max(0,math.floor(angle*1023.0+.5)))
    game_hand = 3 if bitangent_sign > 0.0 else 1
    return normal_word | (angle_bits << 20) | (game_hand << 30)


def decode_uv0_frame(word,author_space=True):
    """Return T, B, N, or None when the frame's valid-hand marker is absent."""
    hand = (word >> 30) & 3
    if hand == 0:
        return None
    n,t0,b0 = _frame(word)
    z = ((word >> 20) & 1023)/1023.0
    x = 4.0*z-1.0 if z < 0.5 else 3.0-4.0*z
    y = (1.0-abs(x))*(-1.0 if z < 0.5 else 1.0)
    x,y = _unit((x,y))
    t = tuple(x*a+y*b for a,b in zip(t0,b0))
    sign = -1.0 if hand == 3 else 1.0
    if author_space:
        sign = -sign
    b = tuple(sign*a for a in _cross(n,t))
    return t,b,n

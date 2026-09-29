import base64

MAX_NICK_LEN = 32
MAX_AVATAR_BYTES = 1 << 20
INJECTION_MARKERS = ("@everyone", "@here", "<@")


def validate_nick(nick):
    """Return an error string if `nick` is unsafe/not allowed, else None."""
    nick = (nick or "").strip()
    if not nick:
        return "Nickname can't be empty."
    if len(nick) > MAX_NICK_LEN:
        return f"Nicknames must be **{MAX_NICK_LEN}** characters or fewer (you used {len(nick)})."
    if any(ord(c) < 32 for c in nick):
        return "That nickname contains invalid control characters."
    low = nick.lower()
    for marker in INJECTION_MARKERS:
        if marker in low:
            return "That nickname contains **mention syntax** (`@everyone`, `@here`, or a user ID), which is not allowed."
    return None


def decode_avatar(avatar):
    """Decode a base64 avatar (optionally a data URL) into bytes, else None."""
    if not avatar:
        return None
    raw = avatar
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:
        return None
import copy


DEFAULT_WELCOMER = {
    "welcome_enabled": False,
    "welcome_channel": "",
    "welcome_text": "Welcome to the server, {user}!",
    "welcome_emoji": "",
    "welcome_image": True,
    "welcome_member_count": True,
    "goodbye_enabled": False,
    "goodbye_channel": "",
    "goodbye_text": "Goodbye, {user}!",
    "goodbye_emoji": "",
    "goodbye_image": True,
    "goodbye_member_count": True,
}

# value spec: (bool,) or (str, min, max)
REQUIRED = {
    "welcome_enabled": (bool,),
    "welcome_channel": (str, 0, 64),
    "welcome_text": (str, 0, 1024),
    "welcome_emoji": (str, 0, 64),
    "welcome_image": (bool,),
    "welcome_member_count": (bool,),
    "goodbye_enabled": (bool,),
    "goodbye_channel": (str, 0, 64),
    "goodbye_text": (str, 0, 1024),
    "goodbye_emoji": (str, 0, 64),
    "goodbye_image": (bool,),
    "goodbye_member_count": (bool,),
}


def defaults():
    return copy.deepcopy(DEFAULT_WELCOMER)


def validate_and_clean(body):
    out = defaults()
    if not isinstance(body, dict):
        return out
    for key, spec in REQUIRED.items():
        if key not in body:
            continue
        val = body[key]
        if spec[0] is bool:
            out[key] = bool(val)
        elif spec[0] is str:
            if not isinstance(val, str):
                continue
            val = val.strip()
            if len(spec) > 2:
                val = val[: spec[2]]
            out[key] = val
    return out


def overrides_for(body):
    if not isinstance(body, dict):
        return {}
    return {k: v for k, v in body.items() if k in DEFAULT_WELCOMER}


def get_welcomer(bot, guild_id):
    cache = getattr(bot, "welcomer_cache", {})
    merged = defaults()
    cached = cache.get(guild_id)
    if isinstance(cached, dict):
        merged.update(cached)
        return merged
    db = getattr(bot, "db", None)
    if db is not None:
        try:
            row = db.get_welcomer(guild_id)
        except Exception:
            row = None
        if isinstance(row, dict):
            merged.update(row)
            cache[guild_id] = merged
            return merged
    return merged


def apply(bot, guild_id, body):
    merged = get_welcomer(bot, guild_id)
    merged.update(validate_and_clean(overrides_for(body)))
    save(bot, guild_id, merged)
    return merged


def save(bot, guild_id, settings):
    cleaned = defaults()
    if isinstance(settings, dict):
        cleaned.update(validate_and_clean(settings))
    getattr(bot, "welcomer_cache", {})[guild_id] = cleaned
    db = getattr(bot, "db", None)
    if db is not None:
        try:
            db.set_welcomer(guild_id, cleaned)
        except Exception:
            pass


def reset(bot, guild_id):
    getattr(bot, "welcomer_cache", {}).pop(guild_id, None)
    db = getattr(bot, "db", None)
    if db is not None:
        try:
            db.delete_welcomer(guild_id)
        except Exception:
            pass
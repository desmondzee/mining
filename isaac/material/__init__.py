def __getattr__(name):
    if name in ("BucketMaterial", "face_colour", "sites_from_world"):
        from . import usd

        return getattr(usd, name)
    raise AttributeError(name)

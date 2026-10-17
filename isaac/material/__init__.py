def __getattr__(name):
    if name in ("BucketMaterial", "face_colour", "sites_from_world"):
        from . import stage

        return getattr(stage, name)
    raise AttributeError(name)

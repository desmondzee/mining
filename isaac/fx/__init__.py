from .presets import PRESETS, resolve


def __getattr__(name):
    if name == "TruckFx":
        from .fx import TruckFx

        return TruckFx
    raise AttributeError(name)

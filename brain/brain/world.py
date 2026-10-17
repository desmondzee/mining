from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class Truck:
    id: str
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    speed: float = 0.0
    goal: tuple[float, float] | None = None
    status: str = "idle"
    throttle: float = 0.0
    steer: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Scan:
    seq: int
    pose: dict
    points: np.ndarray


@dataclass
class World:
    trucks: dict[str, Truck] = field(default_factory=dict)
    bounds: list[float] = field(default_factory=lambda: [-100.0, -100.0, 100.0, 100.0])
    cameras: list[str] = field(default_factory=list)
    lidar: dict[str, Scan] = field(default_factory=dict)
    obstacles: list[dict] = field(default_factory=list)
    paths: list[list[dict]] = field(default_factory=list)
    pedestrians: list[dict] = field(default_factory=list)
    truck_model: str = ""
    sim_connected: bool = False

    def truck(self, truck_id: str) -> Truck:
        if truck_id not in self.trucks:
            self.trucks[truck_id] = Truck(truck_id)
        return self.trucks[truck_id]

    def snapshot(self) -> dict:
        return {
            "sim_connected": self.sim_connected,
            "bounds": self.bounds,
            "cameras": self.cameras,
            "truck_model": self.truck_model,
            "obstacles": self.obstacles,
            "paths": self.paths,
            "pedestrians": self.pedestrians,
            "trucks": [t.to_dict() for t in self.trucks.values()],
        }

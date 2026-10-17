"""Shared state: what the brain knows about the trucks, and the goals users set."""

from dataclasses import dataclass, field


@dataclass
class Truck:
    id: str
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0  # degrees, Unity convention: 0 = +z, clockwise positive
    speed: float = 0.0
    goal: tuple[float, float] | None = None
    status: str = "idle"
    throttle: float = 0.0
    steer: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "z": self.z,
            "yaw": self.yaw,
            "speed": self.speed,
            "goal": list(self.goal) if self.goal else None,
            "status": self.status,
            "throttle": self.throttle,
            "steer": self.steer,
        }


@dataclass
class World:
    trucks: dict[str, Truck] = field(default_factory=dict)
    # [xmin, zmin, xmax, zmax] in world metres, reported by the sim.
    bounds: list[float] = field(default_factory=lambda: [-100.0, -100.0, 100.0, 100.0])
    cameras: list[str] = field(default_factory=list)
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
            "trucks": [t.to_dict() for t in self.trucks.values()],
        }

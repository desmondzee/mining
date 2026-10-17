from dataclasses import asdict, dataclass, field

import numpy as np

from .routing import RoadGraph


@dataclass
class Truck:
    id: str
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    speed: float = 0.0
    goal: tuple[float, float] | None = None
    goal_site: str | None = None
    route: list[list[float]] = field(default_factory=list)
    route_s: float = 0.0
    status: str = "idle"
    throttle: float = 0.0
    steer: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def set_goal(self, goal: tuple[float, float] | None, site: str | None = None, route: list | None = None) -> None:
        self.goal, self.goal_site, self.route, self.route_s = goal, site, route or [], 0.0
        self.status = "driving" if goal else "idle"


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
    roads: list[dict] = field(default_factory=list)
    sites: list[dict] = field(default_factory=list)
    junctions: list[dict] = field(default_factory=list)
    graph: RoadGraph | None = None
    world_seq: int = 0

    def set_network(self, roads: list[dict], sites: list[dict], junctions: list[dict]) -> None:
        self.roads, self.sites, self.junctions = roads, sites, junctions
        graph = RoadGraph(roads, sites, junctions)
        self.graph = graph if graph.roads else None
        self.world_seq += 1

    def network(self) -> dict:
        return {"bounds": self.bounds, "roads": self.roads, "sites": self.sites, "junctions": self.junctions}

    def truck(self, truck_id: str) -> Truck:
        if truck_id not in self.trucks:
            self.trucks[truck_id] = Truck(truck_id)
        return self.trucks[truck_id]

    def snapshot(self, network: bool = True) -> dict:
        return {
            **(self.network() if network else {}),
            "world_seq": self.world_seq,
            "sim_connected": self.sim_connected,
            "bounds": self.bounds,
            "cameras": self.cameras,
            "truck_model": self.truck_model,
            "obstacles": self.obstacles,
            "paths": self.paths,
            "pedestrians": self.pedestrians,
            "trucks": [t.to_dict() for t in self.trucks.values()],
        }

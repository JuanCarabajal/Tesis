from typing import Tuple, Optional, List, Dict

class ZoneIndex:
    def __init__(self, map_config: Dict):
        self.layers = map_config.get("z_layers", [])
        self.zones  = map_config.get("zones", [])

    @staticmethod
    def _in_poly(x: float, y: float, poly: List[Tuple[float, float]]) -> bool:
        inside, n = False, len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            if ((y1 > y) != (y2 > y)):
                xinters = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
                if x < xinters:
                    inside = not inside
        return inside

    def zone_of(self, x: float, y: float, z: float) -> Optional[str]:
        for layer in self.layers:
            if layer["z_min"] <= z <= layer["z_max"]:
                for zc in self.zones:
                    if zc.get("layer") == layer["name"] and self._in_poly(x, y, zc["polygon"]):
                        return zc["id"]
        return None

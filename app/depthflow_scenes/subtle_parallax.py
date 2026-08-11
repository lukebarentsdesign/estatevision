"""Bundled DepthFlow scene for interior-shot depth parallax (spec §4 `standard`).

DepthFlow's CLI has no built-in animation preset as of this writing -- the
project's own docs say presets are "a future release." Producing actual
parallax motion requires a custom `DepthScene` subclass overriding `update()`,
which this file provides. Pattern confirmed against DepthFlow's own
`examples/presets.py` (github.com/BrokenSource/DepthFlow) and matches a
previously-verified working Colab test: sine-wave `self.state.offset` driven
by `self.cycle` is what actually produces visible motion -- the default scene
sits static otherwise.

Invoked as: `python subtle_parallax.py input -i <image> main -o <output> -t <duration>`
(the `scene.cli.meta(sys.argv[1:])` pattern is DepthFlow's own supported entry
point, shown in its official examples.)
"""

import math
import sys

from attrs import define
from depthflow.scene import DepthScene


@define
class SubtleParallax(DepthScene):
    """Gentle horizontal drift -- deliberately subtle for real-estate interiors.

    Kept well short of DepthFlow's own `Horizontal`/`Circle` example intensity
    (0.80 / 0.50) since large parallax on room photos reads as distortion
    rather than cinematic motion.
    """

    def update(self):
        intensity = 0.15
        self.state.offset = (intensity * math.sin(self.cycle), 0.0)
        self.state.isometric = 0.20
        self.state.steady = 0.30


if __name__ == "__main__":
    scene = SubtleParallax()
    scene.cli.meta(sys.argv[1:])

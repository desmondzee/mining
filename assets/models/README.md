# Models

`cat_785c/cat_785c.glb`: CAT 785C haul truck, recoloured into CAT livery by `recolor_truck.py`
(the untextured original, `cat_785c_source.glb`, stays local and isn't committed).

Unity: install glTFast (`com.unity.cloud.gltfast`), drop the GLB into Assets.
**Scale:** the model is about 1.93 m long as exported. Scale it uniformly to the real overall length
of a 785-class truck from the `design` block in world.json / envgen/DESIGN_SPEC.md.
Parts are separate meshes (wheels, dump body, cab), so wheels can spin and the bed can tip.

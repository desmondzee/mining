import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const MAX_POINTS = 20000;
const COLOUR_RANGE = 100;

export function createLidarView(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(devicePixelRatio);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 500);
  camera.position.set(0, 30, 25);
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0, -35);
  controls.update();

  const positions = new Float32Array(MAX_POINTS * 3);
  const colours = new Float32Array(MAX_POINTS * 3);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));
  geometry.setDrawRange(0, 0);
  scene.add(new THREE.Points(geometry, new THREE.PointsMaterial({ size: 0.35, vertexColors: true })));

  const marker = new THREE.Mesh(new THREE.ConeGeometry(1, 3, 12), new THREE.MeshBasicMaterial({ color: 0x8a90c0 }));
  marker.rotation.x = -Math.PI / 2;
  scene.add(marker);

  new ResizeObserver(() => {
    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    camera.aspect = canvas.clientWidth / canvas.clientHeight;
    camera.updateProjectionMatrix();
  }).observe(canvas);
  renderer.setAnimationLoop(() => renderer.render(scene, camera));

  const colour = new THREE.Color();
  return {
    update(points) {
      const n = Math.min(points.length / 3, MAX_POINTS);
      for (let i = 0; i < n; i++) {
        const x = points[3 * i], y = points[3 * i + 1], z = points[3 * i + 2];
        positions.set([x, y, -z], 3 * i);
        const d = Math.min(Math.hypot(x, z) / COLOUR_RANGE, 1);
        colour.setHSL(0.64 - 0.06 * d, 0.42 - 0.3 * d, 0.68 - 0.24 * d);
        colours.set([colour.r, colour.g, colour.b], 3 * i);
      }
      geometry.setDrawRange(0, n);
      geometry.attributes.position.needsUpdate = true;
      geometry.attributes.color.needsUpdate = true;
    },
    clear() {
      geometry.setDrawRange(0, 0);
    },
  };
}

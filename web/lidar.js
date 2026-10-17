import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const MAX_POINTS = 20000;
const COLOUR_RANGE = 100;

function turbo(t) {
  const r = 0.13572138 + t * (4.6153926 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
  const g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
  const b = 0.1066733 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
  const k = (v) => Math.max(0, Math.min(1, 0.12 + 0.88 * v));
  return [k(r), k(g), k(b)];
}

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
        const [r, g, b] = turbo(0.08 + 0.84 * (1 - Math.min(Math.hypot(x, z) / COLOUR_RANGE, 1)));
        colour.setRGB(r, g, b, THREE.SRGBColorSpace);
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

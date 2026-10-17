async function inflate(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Response(stream).arrayBuffer();
}

export function lidarDecoder(onPoints) {
  let busy = false;
  return async (ev) => {
    if (busy) return;
    busy = true;
    try {
      const n = new Uint8Array(ev.data, 0, 1)[0];
      const planes = new Int16Array(await inflate(new Uint8Array(ev.data, 1 + n + 12)));
      const count = planes.length / 3;
      const out = new Float32Array(planes.length);
      for (let axis = 0; axis < 3; axis++) {
        let v = 0;
        for (let i = 0; i < count; i++) {
          v = (v + planes[axis * count + i]) << 16 >> 16;
          out[i * 3 + axis] = v / 100;
        }
      }
      onPoints(out);
    } finally {
      busy = false;
    }
  };
}
